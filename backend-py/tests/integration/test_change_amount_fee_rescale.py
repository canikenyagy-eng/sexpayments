"""
Integration tests for ``OrderService.change_amount`` rescaling the trader reward
(``trader_fee_usdt``) PROPORTIONALLY with the order amount — against a REAL
in-memory ledger (no mocked finance). Audit fix #19.

Why this matters (the bug it guards):
  A dispute round-trip can change an order's amount while it is DISPUTED (the
  collateral sits frozen in trader ESCROW, nothing settled). ``change_amount``
  re-projects the order's USDT figures from the SNAPSHOT ``exchange_rate`` AND
  must rescale ``trader_fee_usdt`` by the same ratio (``new/old``). Otherwise a
  later re-settlement (DISPUTED → SUCCESS) pays the trader the STALE reward for
  the original amount — a real-money leak/short-pay.

  The fix:  new_trader_fee = (trader_fee_usdt / old_amount_usdt * new_amount_usdt)
            .quantize(Decimal("0.0000"))   guarded by trader_fee>0 and old>0.

Harness note (SQLite): SELECT … FOR UPDATE is a no-op here, so concurrency can't
be exercised. Instead we assert (a) EXACT Decimal money math through the real
ledger after the rescale + settle, and (b) SEQUENTIAL idempotency — change the
amount twice and settle, asserting the trader REWARD ledger leg pays the FINAL
rescaled fee exactly once.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select, func

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.orders.service import OrderService
from app.modules.users.models import User

# ── canonical figures ──────────────────────────────────────────────────
RATE = Decimal("100.0000")          # fiat per USDT (snapshot exchange_rate)
FIAT = Decimal("1000.00")           # starting fiat amount → amount_usdt 10
AMOUNT_USDT = Decimal("10.0000")    # FIAT / RATE
MERCHANT_FEE_PCT = Decimal("5.0")   # merchant fees["sbp"] %
FEE_USDT = Decimal("0.5000")        # 10 * 5%
TRADER_FEE = Decimal("0.2000")      # trader reward for amount_usdt 10
SBP = "sbp"


@pytest.fixture(autouse=True)
def _stub_celery():
    """change_amount fires the merchant callback via Celery — stub the broker."""
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.workers.celery_app.celery_app", celery, create=True), \
         patch("app.modules.orders.service.celery_app", celery, create=True):
        yield celery


# ── builders (self-contained, mirror test_dispute_money_flow.py) ───────


async def _mk_user(session, *, username, role=UserRole.TRADER) -> User:
    u = User(
        username=username, password=get_password_hash("pass12345"), role=role,
        totp_enabled=False, is_blocked=False, use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_merchant(session, *, user_id, suffix, fees=None) -> Merchant:
    m = Merchant(
        user_id=user_id, name=f"M-{suffix}", status=TerminalStatus.ENABLED,
        currency=Currency.RUB, api_key=f"key-{suffix}", api_secret=f"secret-{suffix}",
        fees=(fees if fees is not None else {SBP: float(MERCHANT_FEE_PCT)}),
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


async def _mk_order(
    session, *, merchant_id, trader_id, status,
    amount=FIAT, amount_usdt=AMOUNT_USDT, fee_usdt=FEE_USDT,
    trader_fee_usdt=TRADER_FEE, exchange_rate=RATE,
) -> Order:
    o = Order(
        uuid=uuid4(), external_id=f"ext-{uuid4().hex[:8]}",
        merchant_id=merchant_id, trader_id=trader_id,
        direction=PaymentDirection.PAYIN, payment_method=PaymentMethod.SBP,
        amount=amount, currency=Currency.RUB,
        amount_usdt=amount_usdt, fee_usdt=fee_usdt,
        profit_usdt=(amount_usdt - fee_usdt) if amount_usdt is not None and fee_usdt is not None else None,
        trader_fee_usdt=trader_fee_usdt, exchange_rate=exchange_rate, status=status,
    )
    session.add(o)
    await session.flush()
    return o


async def _bal(session, *, user_id=None, merchant_id=None, is_system=False, btype=BalanceType.WORK) -> Decimal:
    stmt = select(Balance).where(Balance.type == btype, Balance.currency == Currency.USDT)
    if is_system:
        stmt = stmt.where(Balance.is_system.is_(True))
    elif user_id is not None:
        stmt = stmt.where(Balance.user_id == user_id)
    elif merchant_id is not None:
        stmt = stmt.where(Balance.merchant_id == merchant_id)
    row = (await session.execute(stmt)).scalars().first()
    return row.amount if row else Decimal("0")


async def _ledger_legs(session, order_id, ref_type) -> list[LedgerEntry]:
    stmt = select(LedgerEntry).where(
        LedgerEntry.reference_type == ref_type,
        LedgerEntry.reference_id == str(order_id),
    )
    return list((await session.execute(stmt)).scalars().all())


async def _setup_disputed(session, *, trader_work=Decimal("100"), order_kwargs=None):
    """A DISPUTED PAYIN order: collateral already frozen in trader ESCROW (the
    pending-like dispute baseline), trader WORK seeded so a positive escrow delta
    can be frozen by change_amount, merchant/system flat."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])

    kwargs = order_kwargs or {}
    order = await _mk_order(
        session, merchant_id=merchant.id, trader_id=trader.id,
        status=OrderStatus.DISPUTED, **kwargs,
    )
    # Collateral frozen in ESCROW; spare WORK to fund any positive recalc delta.
    await _mk_balance(session, order.amount_usdt or Decimal("0"), user_id=trader.id, btype=BalanceType.ESCROW)
    if trader_work:
        await _mk_balance(session, trader_work, user_id=trader.id, btype=BalanceType.WORK)
    return trader, merchant, order


# ════════════════════════════════════════════════════════════════════════
# CORRECT BEHAVIOUR
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_change_amount_rescales_trader_fee_and_settles_new_reward(session):
    """Raise the amount ×2 on a DISPUTED order → trader_fee_usdt rescales
    proportionally (0.2000 → 0.4000), the escrow is topped up by the delta, and a
    subsequent DISPUTED→SUCCESS settlement pays the trader the NEW (rescaled)
    reward — not the stale 0.2000."""
    trader, merchant, order = await _setup_disputed(session)
    svc = OrderService(session)

    out = await svc.change_amount(order, Decimal("2000.00"), fire_callback=False)

    # Persisted figures re-projected from the snapshot rate (rate 100).
    refreshed = await session.get(Order, order.id)
    assert refreshed.amount == Decimal("2000.00")
    assert refreshed.amount_usdt == Decimal("20.0000")
    assert refreshed.fee_usdt == Decimal("1.0000")             # 20 * 5%
    assert refreshed.profit_usdt == Decimal("19.0000")
    # The headline assertion: trader reward rescaled 0.2 * (20/10) = 0.4000.
    assert refreshed.trader_fee_usdt == Decimal("0.4000")
    assert out.trader_fee_usdt == Decimal("0.4000")

    # Escrow topped up by the +10 delta (10 → 20); WORK debited the same.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("20.0000")
    assert await _bal(session, user_id=trader.id, btype=BalanceType.WORK) == Decimal("100") - Decimal("10")

    # Now settle the dispute in the trader's favour → SUCCESS pays the NEW reward.
    await svc.change_status(order, OrderStatus.SUCCESS, fire_callback=False, audit_action=None)

    # Trader WORK = leftover (90) + rescaled reward (0.4). Merchant net 19. System fee−reward.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.WORK) == Decimal("90") + Decimal("0.4000")
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, merchant_id=merchant.id) == Decimal("19.0000")
    assert await _bal(session, is_system=True) == Decimal("1.0000") - Decimal("0.4000")

    # The TRADER_REWARD ledger leg paid EXACTLY the new fee (one leg, value 0.4).
    rewards = await _ledger_legs(session, order.id, LedgerReferenceType.TRADER_REWARD)
    assert len(rewards) == 1
    assert rewards[0].amount == Decimal("0.4000")
    # No stale 0.2 reward anywhere in the ledger.
    assert all(r.amount != Decimal("0.2000") for r in rewards)


@pytest.mark.asyncio
async def test_lower_amount_rescales_trader_fee_down(session):
    """Lowering the amount rescales the reward DOWN proportionally and releases
    the escrow delta back to the trader's WORK."""
    trader, merchant, order = await _setup_disputed(session)
    svc = OrderService(session)

    # 1000 → 600 fiat ⇒ amount_usdt 10 → 6; trader fee 0.20 * (6/10) = 0.1200.
    out = await svc.change_amount(order, Decimal("600.00"), fire_callback=False)

    assert out.amount_usdt == Decimal("6.0000")
    assert out.trader_fee_usdt == Decimal("0.1200")
    # Escrow released by 4 (10 → 6); WORK credited the released delta.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("6.0000")
    assert await _bal(session, user_id=trader.id, btype=BalanceType.WORK) == Decimal("100") + Decimal("4")

    await svc.change_status(order, OrderStatus.SUCCESS, fire_callback=False, audit_action=None)
    rewards = await _ledger_legs(session, order.id, LedgerReferenceType.TRADER_REWARD)
    assert len(rewards) == 1 and rewards[0].amount == Decimal("0.1200")


@pytest.mark.asyncio
async def test_rounding_quantizes_to_four_dp_exactly(session):
    """A ratio that needs rounding lands EXACTLY on quantize(0.0000) — no
    floating drift, no extra precision in the persisted reward or the ledger."""
    # amount_usdt 3, fee 0.1000 → change to amount_usdt 10 ⇒ 0.1 * (10/3)
    # = 0.33333... → quantized 0.3333.
    trader, merchant, order = await _setup_disputed(
        session, trader_work=Decimal("100"),
        order_kwargs=dict(
            amount=Decimal("300.00"), amount_usdt=Decimal("3.0000"),
            fee_usdt=Decimal("0.1500"), trader_fee_usdt=Decimal("0.1000"),
        ),
    )
    svc = OrderService(session)

    out = await svc.change_amount(order, Decimal("1000.00"), fire_callback=False)

    assert out.amount_usdt == Decimal("10.0000")
    assert out.trader_fee_usdt == Decimal("0.3333")
    # exactly 4 dp, no trailing garbage
    assert out.trader_fee_usdt == out.trader_fee_usdt.quantize(Decimal("0.0000"))

    await svc.change_status(order, OrderStatus.SUCCESS, fire_callback=False, audit_action=None)
    rewards = await _ledger_legs(session, order.id, LedgerReferenceType.TRADER_REWARD)
    assert len(rewards) == 1 and rewards[0].amount == Decimal("0.3333")


@pytest.mark.asyncio
async def test_sequential_change_amount_then_settle_pays_final_fee_once(session):
    """SEQUENTIAL idempotency proxy: change the amount TWICE (10→20→15) before
    settling. Each change rescales from the CURRENT persisted values, and the
    single settlement pays the trader the FINAL rescaled fee exactly once — no
    accumulation of stale reward legs."""
    trader, merchant, order = await _setup_disputed(session, trader_work=Decimal("100"))
    svc = OrderService(session)

    # 1000 → 2000: fee 0.2 → 0.4 ; then 2000 → 1500: fee 0.4 * (15/20) = 0.3000.
    await svc.change_amount(order, Decimal("2000.00"), fire_callback=False)
    out = await svc.change_amount(order, Decimal("1500.00"), fire_callback=False)

    assert out.amount_usdt == Decimal("15.0000")
    assert out.trader_fee_usdt == Decimal("0.3000")

    await svc.change_status(order, OrderStatus.SUCCESS, fire_callback=False, audit_action=None)

    rewards = await _ledger_legs(session, order.id, LedgerReferenceType.TRADER_REWARD)
    assert len(rewards) == 1, "exactly one trader-reward settlement leg"
    assert rewards[0].amount == Decimal("0.3000")
    # And the merchant got the final net (15 - fee 0.75).
    assert await _bal(session, merchant_id=merchant.id) == Decimal("15.0000") - Decimal("0.7500")


@pytest.mark.asyncio
async def test_value_conserved_across_change_amount_and_settle(session):
    """No money is created or destroyed by the change_amount + settle sequence:
    sum of all balances after == trader's collateral + reward funding seeded."""
    trader, merchant, order = await _setup_disputed(session, trader_work=Decimal("100"))
    svc = OrderService(session)

    # Total real money in the system before the flow: trader ESCROW(10) + WORK(100).
    before = (
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW)
        + await _bal(session, user_id=trader.id, btype=BalanceType.WORK)
        + await _bal(session, merchant_id=merchant.id)
        + await _bal(session, is_system=True)
    )
    assert before == Decimal("110.0000")

    await svc.change_amount(order, Decimal("2000.00"), fire_callback=False)
    await svc.change_status(order, OrderStatus.SUCCESS, fire_callback=False, audit_action=None)

    after = (
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW)
        + await _bal(session, user_id=trader.id, btype=BalanceType.WORK)
        + await _bal(session, merchant_id=merchant.id)
        + await _bal(session, is_system=True)
    )
    # change_amount froze +10 of WORK into ESCROW then settlement moved it out —
    # conservation holds across the whole sequence.
    assert after == before


# ════════════════════════════════════════════════════════════════════════
# ADVERSARIAL — "try to break it"
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_old_amount_usdt_zero_no_rescale_no_zerodivision(session):
    """old amount_usdt == 0 → the rescale guard short-circuits: NO ZeroDivision,
    NO crash, trader_fee_usdt untouched (the rescale needs a non-zero base)."""
    trader, merchant, order = await _setup_disputed(
        session,
        order_kwargs=dict(
            amount=Decimal("0.00"), amount_usdt=Decimal("0.0000"),
            fee_usdt=Decimal("0.0000"), trader_fee_usdt=Decimal("0.2000"),
        ),
    )
    svc = OrderService(session)

    # Must not raise ZeroDivisionError.
    out = await svc.change_amount(order, Decimal("500.00"), fire_callback=False)

    # amount_usdt re-projects (0 → 5), but the reward is NOT rescaled (old==0).
    assert out.amount_usdt == Decimal("5.0000")
    assert out.trader_fee_usdt == Decimal("0.2000")  # unchanged — no rescale leg ran
    refreshed = await session.get(Order, order.id)
    assert refreshed.trader_fee_usdt == Decimal("0.2000")


@pytest.mark.asyncio
async def test_no_exchange_rate_no_rescale_and_no_usdt_reproject(session):
    """No exchange_rate snapshot → the whole reproject block is skipped: amount_usdt,
    fee and trader_fee are left as-is (can't reproject without a rate)."""
    trader, merchant, order = await _setup_disputed(
        session, order_kwargs=dict(exchange_rate=None),
    )
    svc = OrderService(session)

    out = await svc.change_amount(order, Decimal("9999.00"), fire_callback=False)

    refreshed = await session.get(Order, order.id)
    # Only the fiat amount changed; USDT figures + reward untouched.
    assert refreshed.amount == Decimal("9999.00")
    assert out.amount_usdt == AMOUNT_USDT          # 10 — not reprojected
    assert out.trader_fee_usdt == TRADER_FEE       # 0.2 — not rescaled
    # No escrow movement either (recalc only runs with a new amount_usdt).
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT_USDT


@pytest.mark.asyncio
@pytest.mark.parametrize("stale_fee", [None, Decimal("0.0000")])
async def test_trader_fee_none_or_zero_no_rescale(session, stale_fee):
    """trader_fee_usdt None/0 → the rescale guard (truthy fee) skips it; the
    amount still reprojects but the reward stays None/0 (nothing to rescale)."""
    trader, merchant, order = await _setup_disputed(
        session, order_kwargs=dict(trader_fee_usdt=stale_fee),
    )
    svc = OrderService(session)

    out = await svc.change_amount(order, Decimal("2000.00"), fire_callback=False)

    assert out.amount_usdt == Decimal("20.0000")   # reprojected
    refreshed = await session.get(Order, order.id)
    # Reward unchanged — None stays None, 0 stays 0 (guard: `if trader_fee_usdt`).
    assert refreshed.trader_fee_usdt == stale_fee


@pytest.mark.asyncio
async def test_identical_amount_is_total_noop(session):
    """Changing to the SAME amount returns early — no reproject, no rescale, no
    ledger entry, escrow untouched."""
    trader, merchant, order = await _setup_disputed(session)
    svc = OrderService(session)

    ledger_before = (await session.execute(
        select(func.count()).select_from(LedgerEntry)
    )).scalar_one()

    out = await svc.change_amount(order, FIAT, fire_callback=False)  # same 1000.00

    assert out is order
    assert out.trader_fee_usdt == TRADER_FEE
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT_USDT
    ledger_after = (await session.execute(
        select(func.count()).select_from(LedgerEntry)
    )).scalar_one()
    assert ledger_after == ledger_before  # no money moved


@pytest.mark.asyncio
async def test_rescaled_fee_does_not_double_settle_on_repeat_success(session):
    """Defensive: after change_amount + SUCCESS, a SECOND →SUCCESS is a no-op
    (same-status guard) and does NOT pay the rescaled reward twice. The
    TRADER_REWARD leg count stays at 1 with the rescaled value."""
    trader, merchant, order = await _setup_disputed(session)
    svc = OrderService(session)

    await svc.change_amount(order, Decimal("2000.00"), fire_callback=False)
    await svc.change_status(order, OrderStatus.SUCCESS, fire_callback=False, audit_action=None)
    # Second settle attempt on the already-SUCCESS order.
    again = await svc.change_status(order, OrderStatus.SUCCESS, fire_callback=False, audit_action=None)

    assert again.status == OrderStatus.SUCCESS
    rewards = await _ledger_legs(session, order.id, LedgerReferenceType.TRADER_REWARD)
    assert len(rewards) == 1, "no double-settle: exactly one rescaled reward leg"
    assert rewards[0].amount == Decimal("0.4000")
    assert await _bal(session, user_id=trader.id, btype=BalanceType.WORK) == Decimal("90") + Decimal("0.4000")
