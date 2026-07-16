"""
Integration tests for the Phase-2 denormalized financial snapshot on the order
(``teamlead_reward_usdt`` / ``platform_profit_usdt`` / ``financials`` JSON),
against a REAL in-memory ledger.

The snapshot is a read-model PROJECTION of the ledger (which stays the single
source of truth). OrderService maintains it on every status / amount transition:

  * SUCCESS  → finalize from the teamlead rewards actually paid; platform profit
               = fee − trader_fee − teamlead_reward; ``settled=True``.
  * FAILED/CANCELED → zeroed (nothing realized); ``settled=False``.
  * amount change → amount-proportional figures refresh.

These pin the snapshot AND the underlying balances (so the projection can't
drift from the ledger).
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.users import UserRole
from app.core.exceptions import ConflictException, ForbiddenException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.orders.schemas.admin import AdminOrderUpdate
from app.modules.orders.service import OrderService
from app.modules.teamleaders.models import TeamleadLink
from app.modules.users.models import User

RATE = Decimal("10")
AMOUNT = Decimal("100.0000")     # amount_usdt / frozen collateral
FEE = Decimal("5.0000")          # merchant commission
TRADER_FEE = Decimal("2.0000")   # trader reward


@pytest.fixture(autouse=True)
def _stub_celery():
    """complete_order/fail_order fire the merchant webhook (orders.service
    binding) + selector feedback (workers binding). Stub both brokers."""
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.modules.orders.service.celery_app", celery), \
         patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


# ── builders ──────────────────────────────────────────────────────────


async def _mk_user(session, *, username, role=UserRole.TRADER) -> User:
    u = User(
        username=username, password=get_password_hash("pass12345"), role=role,
        totp_enabled=False, is_blocked=False, use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_merchant(session, *, user_id, suffix) -> Merchant:
    m = Merchant(
        user_id=user_id, name=f"M-{suffix}", status=TerminalStatus.ENABLED,
        currency=Currency.RUB, api_key=f"key-{suffix}", api_secret=f"secret-{suffix}",
        fees={PaymentMethod.SBP.value: 5},
    )
    session.add(m)
    await session.flush()
    return m


async def _mk_balance(session, amount, *, user_id=None, merchant_id=None, is_system=False, btype=BalanceType.WORK) -> Balance:
    b = Balance(
        user_id=user_id, merchant_id=merchant_id, is_system=is_system,
        type=btype, currency=Currency.USDT, amount=Decimal(amount),
    )
    session.add(b)
    await session.flush()
    return b


async def _mk_link(session, *, teamlead_id, entity_type, entity_id, fee_percent) -> TeamleadLink:
    link = TeamleadLink(
        teamlead_id=teamlead_id, linked_entity_type=entity_type,
        linked_entity_id=entity_id, fee_percent=Decimal(str(fee_percent)), is_active=True,
    )
    session.add(link)
    await session.flush()
    return link


async def _mk_order(session, *, merchant_id, trader_id, status=OrderStatus.PENDING) -> Order:
    o = Order(
        uuid=uuid4(), external_id=f"ext-{uuid4().hex[:8]}", merchant_id=merchant_id,
        trader_id=trader_id, direction=PaymentDirection.PAYIN, payment_method=PaymentMethod.SBP,
        amount=Decimal("1000.00"), currency=Currency.RUB, exchange_rate=RATE,
        amount_usdt=AMOUNT, fee_usdt=FEE, trader_fee_usdt=TRADER_FEE,
        profit_usdt=AMOUNT - FEE, status=status,
    )
    session.add(o)
    await session.flush()
    return o


async def _bal(session, *, user_id=None, merchant_id=None, is_system=False, btype=BalanceType.WORK) -> Decimal:
    from sqlalchemy import select
    stmt = select(Balance).where(Balance.type == btype, Balance.currency == Currency.USDT)
    if is_system:
        stmt = stmt.where(Balance.is_system.is_(True))
    elif user_id is not None:
        stmt = stmt.where(Balance.user_id == user_id)
    elif merchant_id is not None:
        stmt = stmt.where(Balance.merchant_id == merchant_id)
    row = (await session.execute(stmt)).scalars().first()
    return row.amount if row else Decimal("0")


# ── SUCCESS: snapshot finalized from paid teamlead rewards ──────────────


@pytest.mark.asyncio
async def test_complete_order_projects_full_financial_snapshot(session):
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    tl_m = await _mk_user(session, username=f"tlm_{uuid4().hex[:6]}", role=UserRole.TEAMLEAD)
    tl_t = await _mk_user(session, username=f"tlt_{uuid4().hex[:6]}", role=UserRole.TEAMLEAD)
    # 1% on the merchant + 0.5% on the trader.
    await _mk_link(session, teamlead_id=tl_m.id, entity_type=UserRole.MERCHANT, entity_id=merchant.id, fee_percent="1.00")
    await _mk_link(session, teamlead_id=tl_t.id, entity_type=UserRole.TRADER, entity_id=trader.id, fee_percent="0.50")
    # Active order: collateral frozen in trader ESCROW.
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.ESCROW)
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id)

    await OrderService(session).complete_order(trader, order.id)

    o = await session.get(Order, order.id)
    # Scalars: reward = 1.0 + 0.5; platform profit = 5 − 2 − 1.5.
    assert o.teamlead_reward_usdt == Decimal("1.5000")
    assert o.platform_profit_usdt == Decimal("1.5000")
    # JSON breakdown carries both teamleads + the headline figures.
    fin = o.financials
    assert fin["settled"] is True and fin["status"] == "success"
    assert fin["teamlead_reward_usdt"] == "1.5000"
    assert fin["platform_profit_usdt"] == "1.5000"
    sides = {r["side"]: r for r in fin["teamlead_rewards"]}
    assert sides["merchant"]["reward_usdt"] == "1.0000"
    assert sides["trader"]["reward_usdt"] == "0.5000"

    # Snapshot agrees with the ledger.
    assert await _bal(session, merchant_id=merchant.id) == Decimal("95")
    assert await _bal(session, user_id=trader.id) == TRADER_FEE
    assert await _bal(session, user_id=tl_m.id) == Decimal("1.0000")
    assert await _bal(session, user_id=tl_t.id) == Decimal("0.5000")
    assert await _bal(session, is_system=True) == Decimal("1.5000")


async def _settled_order_with_teamlead(session):
    """A completed SUCCESS order carrying a 1% merchant teamlead reward (= 1 USDT).
    After complete: merchant 95, trader 2, teamlead 1, system 5−2−1 = 2."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    tl = await _mk_user(session, username=f"tl_{uuid4().hex[:6]}", role=UserRole.TEAMLEAD)
    await _mk_link(session, teamlead_id=tl.id, entity_type=UserRole.MERCHANT, entity_id=merchant.id, fee_percent="1.00")
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.ESCROW)
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id)
    with patch("app.modules.orders.service.celery_app"):
        await OrderService(session).complete_order(trader, order.id)
    return trader, merchant, tl, order


@pytest.mark.asyncio
async def test_dispute_open_on_success_with_teamleads_reverses_them(session):
    """Opening a dispute on a SUCCESS order that paid teamlead rewards must
    reverse those rewards too — otherwise the system balance can't fund the fee
    reversal (transfer enforces funds on the system balance). After open: the
    full collateral is back in trader ESCROW and EVERY other balance is zero."""
    trader, merchant, tl, order = await _settled_order_with_teamlead(session)
    assert await _bal(session, user_id=tl.id) == Decimal("1.0000")  # paid on success

    fresh = await session.get(Order, order.id)
    with patch("app.modules.orders.service.celery_app"):
        await OrderService(session).change_status(fresh, OrderStatus.DISPUTED, audit_action=None)

    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT
    assert await _bal(session, user_id=trader.id) == Decimal("0")
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")
    assert await _bal(session, user_id=tl.id) == Decimal("0")       # teamlead reversed
    assert await _bal(session, is_system=True) == Decimal("0")


@pytest.mark.asyncio
async def test_dispute_open_then_resolve_repays_teamleads(session):
    """open (reverse) → resolve (re-settle) round-trips back to the success shape,
    incl. re-paying the teamlead. Repeat-safe reverse handles the reused refs."""
    trader, merchant, tl, order = await _settled_order_with_teamlead(session)

    svc = OrderService(session)
    with patch("app.modules.orders.service.celery_app"):
        fresh = await session.get(Order, order.id)
        await svc.change_status(fresh, OrderStatus.DISPUTED, audit_action=None)   # reverse
        fresh = await session.get(Order, order.id)
        await svc.change_status(fresh, OrderStatus.SUCCESS, audit_action=None)    # re-settle

    assert await _bal(session, merchant_id=merchant.id) == Decimal("95")
    assert await _bal(session, user_id=trader.id) == TRADER_FEE
    assert await _bal(session, user_id=tl.id) == Decimal("1.0000")  # re-paid
    assert await _bal(session, is_system=True) == Decimal("2.0000")  # 5 − 2 − 1


@pytest.mark.asyncio
async def test_dispute_open_then_reject_leaves_teamleads_reversed(session):
    """open (reverse) → reject (release) → trader keeps the full collateral and
    the teamlead reward stays reversed (order is unsuccessful)."""
    trader, merchant, tl, order = await _settled_order_with_teamlead(session)

    svc = OrderService(session)
    with patch("app.modules.orders.service.celery_app"):
        fresh = await session.get(Order, order.id)
        await svc.change_status(fresh, OrderStatus.DISPUTED, audit_action=None)
        fresh = await session.get(Order, order.id)
        await svc.change_status(fresh, OrderStatus.FAILED, audit_action=None)

    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, user_id=tl.id) == Decimal("0")
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")


@pytest.mark.asyncio
async def test_force_terminal_overrides_reverse_and_repay_teamlead(session):
    """Админ-force БЕЗ диспута: при SUCCESS→FAILED награда тимлида СНИМАЕТСЯ,
    при FAILED→SUCCESS — НАЧИСЛЯЕТСЯ заново (тот же reconcile/complete, что и
    в диспуте, только через force-оверрайд)."""
    trader, merchant, tl, order = await _settled_order_with_teamlead(session)
    assert await _bal(session, user_id=tl.id) == Decimal("1.0000")  # выплачено на success

    svc = OrderService(session)
    with patch("app.modules.orders.service.celery_app"):
        # SUCCESS → FAILED (force): награда тимлида снимается.
        fresh = await session.get(Order, order.id)
        await svc.change_status(fresh, OrderStatus.FAILED, force=True, audit_action=None)
        assert await _bal(session, user_id=tl.id) == Decimal("0")
        assert await _bal(session, user_id=trader.id) == AMOUNT  # залог у трейдера

        # FAILED → SUCCESS (force): награда тимлида начисляется заново.
        fresh = await session.get(Order, order.id)
        await svc.change_status(fresh, OrderStatus.SUCCESS, force=True, audit_action=None)
        assert await _bal(session, user_id=tl.id) == Decimal("1.0000")
        assert await _bal(session, merchant_id=merchant.id) == Decimal("95")


@pytest.mark.asyncio
async def test_complete_order_no_teamleads_profit_is_full_margin(session):
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.ESCROW)
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id)

    await OrderService(session).complete_order(trader, order.id)

    o = await session.get(Order, order.id)
    assert o.teamlead_reward_usdt == Decimal("0")
    assert o.platform_profit_usdt == Decimal("3.0000")   # 5 − 2 − 0
    assert o.financials["teamlead_rewards"] == []
    assert o.financials["settled"] is True


# ── FAILED: snapshot zeroed ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fail_order_zeroes_financial_snapshot(session):
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.ESCROW)
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id)

    await OrderService(session).fail_order(order.id, "timeout", trader=trader)

    o = await session.get(Order, order.id)
    assert o.teamlead_reward_usdt == Decimal("0")
    assert o.platform_profit_usdt == Decimal("0")
    assert o.financials["settled"] is False
    # Collateral returned to the trader (snapshot consistent with the ledger).
    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")


# ── amount change: amount-proportional figures refresh ──────────────────


@pytest.mark.asyncio
async def test_change_amount_refreshes_snapshot_amounts(session):
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.ESCROW)
    await _mk_balance(session, Decimal("20"), user_id=trader.id)   # buffer to freeze more
    # Amount edits are only allowed while a dispute is active.
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.DISPUTED)

    # 1000 → 1200 fiat ⇒ amount_usdt 100 → 120.
    updated = await OrderService(session).admin_update_order(
        order.id, AdminOrderUpdate(amount=1200.0), admin_user_id=1,
    )

    assert updated.financials["amount_usdt"] == "120.0000"
    assert updated.financials["settled"] is False        # DISPUTED — not settled
    assert updated.teamlead_reward_usdt == Decimal("0")
    # ESCROW rebalanced to the new amount.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("120.0000")


# ── state machine: CREATED → PENDING freeze + transition guard ──────────


@pytest.mark.asyncio
async def test_change_status_to_pending_freezes_collateral(session):
    """CREATED → PENDING through the funnel freezes the trader's collateral into
    ESCROW (the assignment money step now lives in change_status)."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    await _mk_balance(session, AMOUNT, user_id=trader.id)   # trader WORK = collateral
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.CREATED)

    with patch("app.modules.orders.service.celery_app"):
        out = await OrderService(session).change_status(order, OrderStatus.PENDING, audit_action=None)

    assert out.status == OrderStatus.PENDING
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT
    assert await _bal(session, user_id=trader.id) == Decimal("0")


@pytest.mark.asyncio
async def test_change_status_rejects_illegal_transition(session):
    """The allowed-transition graph rejects an illegal move (SUCCESS → PENDING),
    so a finalized order can't be silently reopened."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.SUCCESS)

    with pytest.raises(ConflictException, match="Illegal order transition"):
        await OrderService(session).change_status(order, OrderStatus.PENDING)


# ── manual override (force): terminal ↔ terminal with correct money ─────


@pytest.mark.asyncio
async def test_force_failed_to_success_settles_from_trader_work(session):
    """Manual override of a FAILED order to SUCCESS: the released collateral is
    re-frozen then settled — trader WORK → merchant (+fee/reward), no dispute."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    # FAILED shape: collateral already released to the trader's WORK.
    await _mk_balance(session, AMOUNT, user_id=trader.id)
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.FAILED)

    with patch("app.modules.orders.service.celery_app"):
        out = await OrderService(session).change_status(
            order, OrderStatus.SUCCESS, force=True, audit_action=None,
        )

    assert out.status == OrderStatus.SUCCESS
    assert await _bal(session, merchant_id=merchant.id) == Decimal("95")   # NET
    assert await _bal(session, user_id=trader.id) == TRADER_FEE            # reward 2
    assert await _bal(session, is_system=True) == Decimal("3")            # fee − reward
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    # Snapshot finalized.
    assert out.financials["settled"] is True


@pytest.mark.asyncio
async def test_force_success_to_failed_unwinds_settlement(session):
    """Manual override of a SUCCESS order back to FAILED: the settlement is
    reversed and the collateral returns to the trader's WORK (value conserved)."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    # SUCCESS shape: merchant has net, trader has reward, system has fee − reward.
    await _mk_balance(session, Decimal("95"), merchant_id=merchant.id)
    await _mk_balance(session, TRADER_FEE, user_id=trader.id)
    await _mk_balance(session, Decimal("3"), is_system=True)
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.SUCCESS)

    with patch("app.modules.orders.service.celery_app"):
        out = await OrderService(session).change_status(
            order, OrderStatus.FAILED, force=True, audit_action=None,
        )

    assert out.status == OrderStatus.FAILED
    # Everything unwound back to the trader's WORK collateral.
    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert out.financials["settled"] is False


# ── trader settles their own failed order (late payment) ────────────────


@pytest.mark.asyncio
async def test_trader_settles_own_failed_order_to_success(session):
    """A trader marks their already-FAILED order paid: collateral re-frozen +
    settled to the merchant. Foreign traders and active orders are rejected."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    other = await _mk_user(session, username=f"ot_{uuid4().hex[:6]}")
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    await _mk_balance(session, AMOUNT, user_id=trader.id)   # released collateral in WORK
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.FAILED)

    svc = OrderService(session)
    # Only the assigned trader may settle it.
    with pytest.raises(ForbiddenException):
        await svc.trader_settle_failed_order(other, order.id)

    with patch("app.modules.orders.service.celery_app"):
        out = await svc.trader_settle_failed_order(trader, order.id)

    assert out.status == OrderStatus.SUCCESS
    assert await _bal(session, merchant_id=merchant.id) == Decimal("95")
    assert await _bal(session, user_id=trader.id) == TRADER_FEE
    assert await _bal(session, is_system=True) == Decimal("3")

    # An active (PENDING) order can't be settled this way — use complete_order.
    active = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.PENDING)
    with pytest.raises(ConflictException):
        await svc.trader_settle_failed_order(trader, active.id)
