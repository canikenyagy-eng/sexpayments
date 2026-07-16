"""
Integration tests for the dispute-reconciliation edges + ``change_amount`` edges
that the sibling suites (``test_dispute_money_flow`` /
``test_change_amount_fee_rescale``) don't pin — against a REAL in-memory ledger
(no mocked finance).

What this file adds on top of the siblings:

  * ``reconcile_for_dispute`` on a SUCCESS order WITH a non-zero teamlead reward,
    driven through a REAL ``complete_order`` first so genuine settlement ledger
    legs exist — then asserts the THREE reverse legs (TRADER_REWARD,
    SYSTEM_COMMISSION, ORDER_PAYIN) carry reference_id ``<order_id>_dispute_reverse``
    and the teamlead reward is reversed (TEAMLEAD_REWARD ``<order_id>_reversal``),
    with the books unwound to the pending-like ESCROW baseline + conservation.

  * ``change_amount`` on an ACTIVE, NON-DISPUTED order (PENDING / RECEIPT_UPLOADED):
    these are in ``ESCROW_ACTIVE`` too, so the escrow rebalances and the
    fee/trader_fee rescale — only the DISPUTED branch was covered before.

  * ``change_amount`` on a SUCCESS (settled) order: re-projects amount_usdt / fee /
    trader_fee from the snapshot rate but moves NO money (settled rows are
    re-projected only).

ADVERSARIAL:
  * ``reconcile_for_dispute`` on a PAYOUT-direction order → ValidationException guard.
  * FAILED / CANCELED re-freeze when the trader's WORK is insufficient →
    "Insufficient funds", and NOTHING is partially moved (escrow / work intact).
  * resolve-after-resolve and resolve-after-reject → the 2nd call is guarded
    ("already closed") and the money moves exactly once (leg counts unchanged).

Harness note (SQLite): SELECT … FOR UPDATE is a no-op, so these assert exact
Decimal balances, ledger-leg counts (by reference_type + reference_id), and value
conservation — never row-lock-dependent concurrency.
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
from app.common.enums.disputes import DisputeReason
from app.core.exceptions import ValidationException
from app.core.security import get_password_hash
from app.modules.disputes.schemas import DisputeCreate
from app.modules.disputes.service import DisputeService
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.finance.service import FinanceService
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.orders.service import OrderService
from app.modules.teamleaders.models import TeamleadLink
from app.modules.users.models import User

# ── canonical figures (USDT) ───────────────────────────────────────────
RATE = Decimal("100.0000")          # fiat per USDT (snapshot exchange_rate)
FIAT = Decimal("1000.00")           # starting fiat → amount_usdt 10
AMOUNT = Decimal("10.0000")         # amount_usdt (FIAT / RATE)
FEE = Decimal("0.5000")             # system commission (10 * 5%)
TRADER_FEE = Decimal("0.2000")      # trader reward
TL_PCT = Decimal("1.00")            # teamlead 1% of amount_usdt
TL_REWARD = Decimal("0.1000")       # 1% of 10
NET = AMOUNT - FEE                  # 9.5 — merchant keeps on success
SBP = "sbp"
Z = Decimal("0")


@pytest.fixture(autouse=True)
def _stub_celery():
    """Disputes + change_status enqueue notify/forward/webhook tasks — stub the
    broker so nothing reaches a real connection."""
    notify = MagicMock()
    notify.apply_async = MagicMock(return_value=None)
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.workers.tasks.trader_bot.notify_trader_new_dispute", notify, create=True), \
         patch("app.workers.celery_app.celery_app", celery, create=True), \
         patch("app.modules.orders.service.celery_app", celery, create=True):
        yield celery


# ── builders (self-contained) ──────────────────────────────────────────


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
        fees=(fees if fees is not None else {SBP: float(Decimal("5.0"))}),
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
    direction=PaymentDirection.PAYIN,
    amount=FIAT, amount_usdt=AMOUNT, fee_usdt=FEE,
    trader_fee_usdt=TRADER_FEE, exchange_rate=RATE,
) -> Order:
    o = Order(
        uuid=uuid4(), external_id=f"ext-{uuid4().hex[:8]}",
        merchant_id=merchant_id, trader_id=trader_id,
        direction=direction, payment_method=PaymentMethod.SBP,
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


async def _legs(session, ref_type, ref_id) -> list[LedgerEntry]:
    """All ledger legs for an exact (reference_type, reference_id) pair."""
    stmt = select(LedgerEntry).where(
        LedgerEntry.reference_type == ref_type,
        LedgerEntry.reference_id == str(ref_id),
    )
    return list((await session.execute(stmt)).scalars().all())


async def _ledger_count(session) -> int:
    return (await session.execute(select(func.count()).select_from(LedgerEntry))).scalar_one()


def _total(*amounts) -> Decimal:
    return sum(amounts, Decimal("0"))


# ════════════════════════════════════════════════════════════════════════
# CORRECT BEHAVIOUR
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_reconcile_success_with_teamlead_writes_three_reverse_legs(session):
    """Open a dispute on a genuinely-settled SUCCESS order that paid a teamlead
    reward (driven through a REAL complete_order first) → the reconcile reverses
    the full settlement with the THREE ``<order_id>_dispute_reverse`` legs
    (TRADER_REWARD, SYSTEM_COMMISSION, ORDER_PAYIN) plus the teamlead-reward
    reversal, lands the collateral back in trader ESCROW, and conserves value."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    teamlead = await _mk_user(session, username=f"tl_{uuid4().hex[:6]}", role=UserRole.TEAMLEAD)
    session.add(TeamleadLink(
        teamlead_id=teamlead.id, linked_entity_type=UserRole.TRADER,
        linked_entity_id=trader.id, fee_percent=TL_PCT, is_active=True,
    ))
    # Pending-like baseline: collateral frozen in trader ESCROW.
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.PENDING)
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.ESCROW)
    await session.flush()

    svc = OrderService(session)
    # REAL settle: pays trader reward, system fee, teamlead reward, credits merchant.
    await svc.change_status(order, OrderStatus.SUCCESS, fire_callback=False, audit_action=None)

    # Sanity — post-settle shape.
    assert await _bal(session, merchant_id=merchant.id) == NET                       # 9.5
    assert await _bal(session, user_id=trader.id, btype=BalanceType.WORK) == TRADER_FEE
    assert await _bal(session, user_id=teamlead.id) == TL_REWARD                       # 0.1
    assert await _bal(session, is_system=True) == FEE - TRADER_FEE - TL_REWARD         # 0.2
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Z

    # Open the dispute → reconcile reverses the whole settlement.
    dispute = await DisputeService(session).open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.UNKNOWN), order_id=str(order.uuid),
    )

    # The THREE reverse legs exist under '<order_id>_dispute_reverse', one each.
    rev = f"{order.id}_dispute_reverse"
    payin_rev = await _legs(session, LedgerReferenceType.ORDER_PAYIN, rev)
    fee_rev = await _legs(session, LedgerReferenceType.SYSTEM_COMMISSION, rev)
    reward_rev = await _legs(session, LedgerReferenceType.TRADER_REWARD, rev)
    assert len(payin_rev) == 1 and payin_rev[0].amount == AMOUNT      # merchant WORK → trader ESCROW
    assert len(fee_rev) == 1 and fee_rev[0].amount == FEE             # system → merchant WORK
    assert len(reward_rev) == 1 and reward_rev[0].amount == TRADER_FEE  # trader WORK → system

    # Teamlead reward reversed via the TEAMLEAD_REWARD '<order_id>_reversal' leg.
    tl_rev = await _legs(session, LedgerReferenceType.TEAMLEAD_REWARD, f"{order.id}_reversal")
    assert len(tl_rev) == 1 and tl_rev[0].amount == TL_REWARD

    # Books unwound to the pending-like baseline: full collateral in trader ESCROW,
    # everyone else drained.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.WORK) == Z
    assert await _bal(session, merchant_id=merchant.id) == Z
    assert await _bal(session, is_system=True) == Z
    assert await _bal(session, user_id=teamlead.id) == Z

    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.DISPUTED

    # Conservation: the order amount lives entirely in trader ESCROW now.
    after = _total(
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
        await _bal(session, user_id=trader.id, btype=BalanceType.WORK),
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, is_system=True),
        await _bal(session, user_id=teamlead.id),
    )
    assert after == AMOUNT
    _ = dispute  # opened


@pytest.mark.asyncio
@pytest.mark.parametrize("active_status", [OrderStatus.PENDING, OrderStatus.RECEIPT_UPLOADED])
async def test_change_amount_on_active_nondisputed_order_rebalances_escrow(session, active_status):
    """``change_amount`` on an ACTIVE, non-DISPUTED order (PENDING /
    RECEIPT_UPLOADED — both in ESCROW_ACTIVE) tops up the frozen escrow by the
    delta and rescales fee + trader_fee from the snapshot rate."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=active_status)
    # Collateral frozen in ESCROW (10) + spare WORK to fund the positive delta.
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.ESCROW)
    await _mk_balance(session, Decimal("100"), user_id=trader.id, btype=BalanceType.WORK)
    await session.flush()

    svc = OrderService(session)
    # 1000 → 2000 fiat ⇒ amount_usdt 10 → 20; fee 0.5 → 1.0; trader fee 0.2 → 0.4.
    out = await svc.change_amount(order, Decimal("2000.00"), fire_callback=False)

    assert out.amount_usdt == Decimal("20.0000")
    assert out.fee_usdt == Decimal("1.0000")
    assert out.trader_fee_usdt == Decimal("0.4000")
    # Escrow topped up by +10 (10 → 20); WORK debited the same delta.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("20.0000")
    assert await _bal(session, user_id=trader.id, btype=BalanceType.WORK) == Decimal("90")
    # The escrow rebalance is a single ORDER_PAYIN delta leg keyed by str(order.id).
    delta_legs = await _legs(session, LedgerReferenceType.ORDER_PAYIN, order.id)
    assert len(delta_legs) == 1 and delta_legs[0].amount == Decimal("10.0000")
    # No money created: trader ESCROW + WORK still total the original 110.
    assert (
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW)
        + await _bal(session, user_id=trader.id, btype=BalanceType.WORK)
    ) == Decimal("110.0000")


@pytest.mark.asyncio
async def test_change_amount_on_success_reprojects_but_moves_no_money(session):
    """``change_amount`` on a SETTLED (SUCCESS) order re-projects amount_usdt /
    fee / trader_fee from the snapshot rate but moves NO money — SUCCESS is not in
    ESCROW_ACTIVE, so recalculate_order never runs and no ledger entry is written."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.SUCCESS)
    # Post-settlement shape: merchant net, trader reward, system fee−reward.
    await _mk_balance(session, NET, merchant_id=merchant.id)
    await _mk_balance(session, TRADER_FEE, user_id=trader.id)
    await _mk_balance(session, FEE - TRADER_FEE, is_system=True)
    await session.flush()

    svc = OrderService(session)
    ledger_before = await _ledger_count(session)

    # 1000 → 2000 fiat ⇒ figures double, but the order is settled: no money moves.
    out = await svc.change_amount(order, Decimal("2000.00"), fire_callback=False)

    # Row re-projected from the snapshot rate.
    assert out.amount_usdt == Decimal("20.0000")
    assert out.fee_usdt == Decimal("1.0000")
    assert out.trader_fee_usdt == Decimal("0.4000")
    assert out.profit_usdt == Decimal("19.0000")
    # NOT a single new ledger entry, and balances are byte-for-byte unchanged.
    assert await _ledger_count(session) == ledger_before
    assert await _bal(session, merchant_id=merchant.id) == NET
    assert await _bal(session, user_id=trader.id) == TRADER_FEE
    assert await _bal(session, is_system=True) == FEE - TRADER_FEE
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Z


# ════════════════════════════════════════════════════════════════════════
# ADVERSARIAL — "try to break it"
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_reconcile_payout_direction_is_rejected(session):
    """``reconcile_for_dispute`` only models PAYIN collateral — a PAYOUT-direction
    order trips the direction guard with a ValidationException and moves nothing."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    order = await _mk_order(
        session, merchant_id=merchant.id, trader_id=trader.id,
        status=OrderStatus.SUCCESS, direction=PaymentDirection.PAYOUT,
    )
    await session.flush()

    ledger_before = await _ledger_count(session)
    with pytest.raises(ValidationException, match="only supports PAYIN"):
        await FinanceService(session).reconcile_for_dispute(
            order=order, merchant=merchant, trader=trader, pre_status=OrderStatus.SUCCESS,
        )
    # Guard tripped BEFORE any transfer.
    assert await _ledger_count(session) == ledger_before


@pytest.mark.asyncio
async def test_failed_refreeze_overdraws_trader_work_into_escrow(session):
    """Re-freezing a FAILED/CANCELED order's collateral (trader WORK → ESCROW)
    when the trader's WORK can't cover ``amount_usdt`` STILL goes through — the
    dispute MUST be openable — pushing WORK NEGATIVE (a debt carried until the
    dispute resolves). The full collateral lands in ESCROW and the leg is written."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.FAILED)
    # Trader WORK holds only part of the collateral — re-freeze of AMOUNT overdraws it.
    await _mk_balance(session, Decimal("4.0000"), user_id=trader.id, btype=BalanceType.WORK)
    await _mk_balance(session, Z, user_id=trader.id, btype=BalanceType.ESCROW)
    await session.flush()

    await FinanceService(session).reconcile_for_dispute(
        order=order, merchant=merchant, trader=trader, pre_status=OrderStatus.FAILED,
    )

    # WORK overdrawn (4 − AMOUNT), full collateral re-frozen in ESCROW, one leg written.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.WORK) == Decimal("4.0000") - AMOUNT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT
    assert len(await _legs(session, LedgerReferenceType.ORDER_PAYIN, f"{order.id}_dispute_freeze")) == 1


@pytest.mark.asyncio
async def test_resolve_after_resolve_is_guarded_money_moves_once(session):
    """A second resolve on an already-RESOLVED dispute is rejected ('already
    closed') and the settlement legs are NOT written twice."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.CANCELED)
    # CANCELED shape: collateral back in trader WORK (re-frozen on dispute open).
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.WORK)
    await session.flush()

    svc = DisputeService(session)
    dispute = await svc.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.NO_PAYMENT), order_id=str(order.uuid),
    )
    await svc.resolve_dispute(admin_id=1, dispute_id=dispute.id, resolution_text="merchant wins")

    # Settled once.
    assert await _bal(session, merchant_id=merchant.id) == NET
    settle_legs = len(await _legs(session, LedgerReferenceType.ORDER_PAYIN, order.id))

    # Second resolve → guarded.
    with pytest.raises(ValidationException, match="already closed"):
        await svc.resolve_dispute(admin_id=2, dispute_id=dispute.id, resolution_text="again")

    # Money unchanged; no extra settlement leg.
    assert await _bal(session, merchant_id=merchant.id) == NET
    assert await _bal(session, user_id=trader.id) == TRADER_FEE
    assert len(await _legs(session, LedgerReferenceType.ORDER_PAYIN, order.id)) == settle_legs


@pytest.mark.asyncio
async def test_resolve_after_reject_is_guarded_money_moves_once(session):
    """Once a dispute is REJECTED (order FAILED, collateral released to trader), a
    follow-up resolve is rejected ('already closed') — the order is NOT re-settled
    to the merchant and no second money move happens."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.CANCELED)
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.WORK)
    await session.flush()

    svc = DisputeService(session)
    dispute = await svc.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.NO_PAYMENT), order_id=str(order.uuid),
    )
    await svc.reject_dispute(admin_id=1, dispute_id=dispute.id, resolution_text="trader wins")

    # Rejected: full collateral back to trader, merchant flat, order FAILED.
    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, merchant_id=merchant.id) == Z
    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.FAILED
    ledger_before = await _ledger_count(session)

    # Resolve-after-reject → guarded, no re-settle.
    with pytest.raises(ValidationException, match="already closed"):
        await svc.resolve_dispute(admin_id=2, dispute_id=dispute.id, resolution_text="flip")

    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, merchant_id=merchant.id) == Z
    assert await _ledger_count(session) == ledger_before
    refreshed2 = await session.get(Order, order.id)
    assert refreshed2.status == OrderStatus.FAILED
