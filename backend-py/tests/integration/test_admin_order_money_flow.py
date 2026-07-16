"""
Integration tests for ``OrderService.admin_update_order`` against a REAL
in-memory ledger (no mocked finance).

Admin can override an active order's **status** and/or **amount**:

  * status → SUCCESS  : settle (trader ESCROW → merchant WORK, system fee,
                        trader reward) on ``order.amount_usdt``.
  * status → CANCELED/FAILED : release the frozen collateral (trader ESCROW →
                        trader WORK).
  * amount change on an active order : rebalance ESCROW by the delta via
                        ``recalculate_order`` (WORK↔ESCROW), and recompute the
                        order's ``amount_usdt`` / ``fee_usdt`` / ``profit_usdt``
                        from the SNAPSHOT ``exchange_rate``.
  * amount + status in one call : the amount delta is rebalanced FIRST so the
                        subsequent settlement operates on the NEW amount — the
                        critical "no Δ leak" ordering.

These pin the exact balances + the recomputed order row, and assert value
conservation. They are the safety net for routing the status transition through
``change_status`` and extracting a public ``change_amount``.
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
from app.core.exceptions import ConflictException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.orders.schemas.admin import AdminOrderUpdate
from app.modules.orders.service import OrderService
from app.modules.users.models import User

# Canonical money shape. RATE ties fiat → USDT so amounts stay round.
RATE = Decimal("10")                 # 1 USDT == 10 fiat
BASE_FIAT = Decimal("1000.00")       # → 100 USDT
AMOUNT = Decimal("100.0000")         # base amount_usdt / frozen collateral
FEE_PCT = 5                          # merchant commission %, keyed by method
FEE = Decimal("5.0000")              # 5% of 100
TRADER_FEE = Decimal("2.0000")       # trader reward (admin does NOT recompute)
NET = AMOUNT - FEE                   # 95 — what the merchant keeps on success


@pytest.fixture(autouse=True)
def _stub_celery():
    """admin_update_order fires the merchant webhook on a status change — stub
    the broker so the test doesn't reach a real Celery."""
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.modules.orders.service.celery_app", celery):
        yield celery


def _callback_count(celery) -> int:
    """How many merchant order-webhooks were enqueued."""
    return sum(
        1 for c in celery.send_task.call_args_list
        if c.args and c.args[0].endswith("callbacks.send_order_callback")
    )


# ── builders ──────────────────────────────────────────────────────────


async def _mk_user(session, *, username, role=UserRole.TRADER) -> User:
    u = User(
        username=username,
        password=get_password_hash("pass12345"),
        role=role,
        totp_enabled=False,
        is_blocked=False,
        use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_merchant(session, *, user_id, suffix) -> Merchant:
    m = Merchant(
        user_id=user_id,
        name=f"M-{suffix}",
        status=TerminalStatus.ENABLED,
        currency=Currency.RUB,
        api_key=f"key-{suffix}",
        api_secret=f"secret-{suffix}",
        fees={PaymentMethod.SBP.value: FEE_PCT},
    )
    session.add(m)
    await session.flush()
    return m


async def _mk_balance(session, amount, *, user_id=None, merchant_id=None, is_system=False, btype=BalanceType.WORK) -> Balance:
    b = Balance(
        user_id=user_id,
        merchant_id=merchant_id,
        is_system=is_system,
        type=btype,
        currency=Currency.USDT,
        amount=Decimal(amount),
    )
    session.add(b)
    await session.flush()
    return b


async def _mk_order(session, *, merchant_id, trader_id, status, amount_usdt=AMOUNT, fee_usdt=FEE) -> Order:
    o = Order(
        uuid=uuid4(),
        external_id=f"ext-{uuid4().hex[:8]}",
        merchant_id=merchant_id,
        trader_id=trader_id,
        direction=PaymentDirection.PAYIN,
        payment_method=PaymentMethod.SBP,
        amount=BASE_FIAT,
        currency=Currency.RUB,
        exchange_rate=RATE,
        amount_usdt=amount_usdt,
        fee_usdt=fee_usdt,
        trader_fee_usdt=TRADER_FEE,
        profit_usdt=amount_usdt - fee_usdt,
        status=status,
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


async def _setup(session, *, order_status, amount_usdt=AMOUNT, fee_usdt=FEE, seed=None):
    """Create trader, merchant-user, merchant, order, seed WORK/ESCROW balances.

    ``seed`` maps a logical balance name → starting amount. For an active order
    the collateral is already frozen in trader ESCROW (``trader_escrow``).
    """
    seed = seed or {}
    trader = await _mk_user(session, username=f"trader_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])

    if seed.get("trader_work"):
        await _mk_balance(session, seed["trader_work"], user_id=trader.id)
    if seed.get("trader_escrow"):
        await _mk_balance(session, seed["trader_escrow"], user_id=trader.id, btype=BalanceType.ESCROW)
    if seed.get("merchant_work"):
        await _mk_balance(session, seed["merchant_work"], merchant_id=merchant.id)
    if seed.get("system_work"):
        await _mk_balance(session, seed["system_work"], is_system=True)

    order = await _mk_order(
        session, merchant_id=merchant.id, trader_id=trader.id,
        status=order_status, amount_usdt=amount_usdt, fee_usdt=fee_usdt,
    )
    return trader, merchant, order


# An active order has its full collateral frozen in trader ESCROW.
ACTIVE_SEED = {"trader_escrow": AMOUNT}


# ── status-only overrides ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_pending_to_success_settles_to_merchant(session):
    trader, merchant, order = await _setup(session, order_status=OrderStatus.PENDING, seed=ACTIVE_SEED)

    await OrderService(session).admin_update_order(
        order.id, AdminOrderUpdate(status=OrderStatus.SUCCESS, reason="admin confirm"),
        admin_user_id=1,
    )

    assert await _bal(session, merchant_id=merchant.id) == NET            # 95
    assert await _bal(session, user_id=trader.id) == TRADER_FEE           # 2
    assert await _bal(session, is_system=True) == FEE - TRADER_FEE        # 3
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.SUCCESS


@pytest.mark.asyncio
async def test_admin_pending_to_canceled_releases_to_trader(session):
    trader, merchant, order = await _setup(session, order_status=OrderStatus.PENDING, seed=ACTIVE_SEED)

    await OrderService(session).admin_update_order(
        order.id, AdminOrderUpdate(status=OrderStatus.CANCELED, reason="admin cancel"),
        admin_user_id=1,
    )

    assert await _bal(session, user_id=trader.id) == AMOUNT              # 100 back to WORK
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")
    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.CANCELED


@pytest.mark.asyncio
async def test_admin_receipt_uploaded_to_failed_releases_to_trader(session):
    trader, merchant, order = await _setup(session, order_status=OrderStatus.RECEIPT_UPLOADED, seed=ACTIVE_SEED)

    await OrderService(session).admin_update_order(
        order.id, AdminOrderUpdate(status=OrderStatus.FAILED, reason="admin fail"),
        admin_user_id=1,
    )

    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.FAILED


# ── amount overrides — ONLY while the order has an active dispute ───────


@pytest.mark.asyncio
async def test_admin_amount_up_freezes_more_into_escrow(session, _stub_celery):
    """+400 fiat → +40 USDT on a DISPUTED order: trader WORK → ESCROW by the
    delta; the order row's amount_usdt / fee_usdt / profit_usdt recompute."""
    trader, merchant, order = await _setup(
        session, order_status=OrderStatus.DISPUTED,
        seed={"trader_escrow": AMOUNT, "trader_work": Decimal("50")},
    )

    updated = await OrderService(session).admin_update_order(
        order.id, AdminOrderUpdate(amount=1400.0), admin_user_id=1,
    )

    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("140.0000")
    assert await _bal(session, user_id=trader.id) == Decimal("10")        # 50 − 40
    # Order row recomputed from rate 10: 1400/10 = 140, fee 5% = 7, profit 133.
    assert updated.amount_usdt == Decimal("140.0000")
    assert updated.fee_usdt == Decimal("7.0000")
    assert updated.profit_usdt == Decimal("133.0000")
    assert updated.status == OrderStatus.DISPUTED       # status untouched
    # Amount change fires the merchant webhook (once).
    assert _callback_count(_stub_celery) == 1


@pytest.mark.asyncio
async def test_admin_amount_down_releases_from_escrow(session):
    """−300 fiat → −30 USDT on a DISPUTED order: trader ESCROW → WORK by delta."""
    trader, merchant, order = await _setup(
        session, order_status=OrderStatus.DISPUTED, seed={"trader_escrow": AMOUNT},
    )

    updated = await OrderService(session).admin_update_order(
        order.id, AdminOrderUpdate(amount=700.0), admin_user_id=1,
    )

    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("70.0000")
    assert await _bal(session, user_id=trader.id) == Decimal("30.0000")
    assert updated.amount_usdt == Decimal("70.0000")
    assert updated.fee_usdt == Decimal("3.5000")


# ── dispute-resolution sequence: adjust amount, then settle (no Δ leak) ─


@pytest.mark.asyncio
async def test_amount_change_during_dispute_then_settle_uses_new_amount(session, _stub_celery):
    """Realistic flow: adjust a DISPUTED order's amount (escrow rebalanced by the
    delta), THEN settle to SUCCESS — the NEW amount is what settles, value
    conserved. (Amount and status are separate ops; they can't combine because a
    DISPUTED status change is blocked while the dispute is open.) The trader
    reward rescales proportionally to the new amount (#19): on +40% it grows
    2.0 → 2.8, shifting 0.8 from the platform to the trader while the total
    stays conserved."""
    trader, merchant, order = await _setup(
        session, order_status=OrderStatus.DISPUTED,
        seed={"trader_escrow": AMOUNT, "trader_work": Decimal("40")},
    )
    svc = OrderService(session)
    before = (
        await _bal(session, user_id=trader.id)
        + await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW)
    )

    # 1) Amount +40 USDT during the active dispute → escrow rebalanced.
    await svc.admin_update_order(order.id, AdminOrderUpdate(amount=1400.0), admin_user_id=1)
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("140.0000")

    # 2) Settle to SUCCESS (DISPUTED→SUCCESS, as a dispute resolve does) → NEW 140.
    fresh = await session.get(Order, order.id)
    await svc.change_status(fresh, OrderStatus.SUCCESS, audit_action=None)

    assert await _bal(session, merchant_id=merchant.id) == Decimal("133.0000")  # 140 − 7
    # Reward rescaled with the new amount (#19): 2.0 × (140/100) = 2.8.
    assert await _bal(session, user_id=trader.id) == Decimal("2.8000")
    assert await _bal(session, is_system=True) == Decimal("4.2000")            # fee 7 − rescaled reward 2.8
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    # Value conserved: started with 100 ESCROW + 40 WORK = 140, ends 133+2+5.
    after = (
        await _bal(session, user_id=trader.id)
        + await _bal(session, merchant_id=merchant.id)
        + await _bal(session, is_system=True)
    )
    assert before == after == Decimal("140.0000")


# ── guards ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_admin_can_force_finalized_order_back_to_terminal(session):
    """Manual override: admin reverts a FINALIZED order without a dispute.
    SUCCESS → CANCELED unwinds the settlement — the collateral returns to the
    trader's WORK and merchant/system are zeroed."""
    trader, merchant, order = await _setup(
        session, order_status=OrderStatus.SUCCESS,
        seed={"merchant_work": NET, "trader_work": TRADER_FEE, "system_work": FEE - TRADER_FEE},
    )

    await OrderService(session).admin_update_order(
        order.id, AdminOrderUpdate(status=OrderStatus.CANCELED, reason="manual reverse"),
        admin_user_id=1,
    )

    assert await _bal(session, user_id=trader.id) == AMOUNT       # collateral back to WORK
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")
    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.CANCELED


@pytest.mark.asyncio
async def test_admin_cannot_change_amount_without_active_dispute(session):
    """An amount change is allowed ONLY while the order has an active dispute.
    On an active-but-undisputed (PENDING) order and on a finalized (SUCCESS)
    order it's rejected — the latter would desync the row / reverse the
    settlement at the wrong amount."""
    # PENDING (active, no dispute) → rejected.
    _t, _m, pending = await _setup(session, order_status=OrderStatus.PENDING, seed=ACTIVE_SEED)
    with pytest.raises(ConflictException):
        await OrderService(session).admin_update_order(
            pending.id, AdminOrderUpdate(amount=1400.0), admin_user_id=1,
        )

    # SUCCESS (finalized) → rejected, settlement untouched.
    trader, merchant, order = await _setup(
        session, order_status=OrderStatus.SUCCESS,
        seed={"merchant_work": NET, "trader_work": TRADER_FEE, "system_work": FEE - TRADER_FEE},
    )
    with pytest.raises(ConflictException):
        await OrderService(session).admin_update_order(
            order.id, AdminOrderUpdate(amount=1400.0), admin_user_id=1,
        )
    assert await _bal(session, merchant_id=merchant.id) == NET
    assert await _bal(session, user_id=trader.id) == TRADER_FEE
    assert await _bal(session, is_system=True) == FEE - TRADER_FEE


@pytest.mark.asyncio
async def test_admin_cannot_set_non_terminal_status(session):
    """The manual override is restricted to terminal statuses — re-activating a
    settled order to PENDING has no sensible money semantics and is rejected."""
    trader, merchant, order = await _setup(
        session, order_status=OrderStatus.SUCCESS,
        seed={"merchant_work": NET, "trader_work": TRADER_FEE, "system_work": FEE - TRADER_FEE},
    )

    with pytest.raises(ConflictException):
        await OrderService(session).admin_update_order(
            order.id, AdminOrderUpdate(status=OrderStatus.PENDING),
            admin_user_id=1,
        )
    # Balances untouched.
    assert await _bal(session, merchant_id=merchant.id) == NET
    assert await _bal(session, user_id=trader.id) == TRADER_FEE


@pytest.mark.asyncio
async def test_admin_noop_update_returns_order_without_money_moves(session):
    """An update that changes nothing is a no-op (no ledger entries)."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.PENDING, seed=ACTIVE_SEED)

    updated = await OrderService(session).admin_update_order(
        order.id, AdminOrderUpdate(), admin_user_id=1,
    )

    assert updated.id == order.id
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT
    assert await _bal(session, user_id=trader.id) == Decimal("0")
