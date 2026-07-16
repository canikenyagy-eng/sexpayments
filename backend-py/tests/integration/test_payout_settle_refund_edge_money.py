"""
Integration tests for PAYOUT freeze / settle / refund / claim EDGE cases against
a REAL in-memory ledger — leg shapes, value conservation, terminal/escrow
starvation, boundary freezes, negative-system settle, and double-op idempotency.

Companion to ``test_payout_money_flow.py`` (happy path). Here we pin:
  * exact leg shapes of settle (with/without merchant_fee, with/without trader_fee),
  * refund leg shape + refund-from-CLAIMED paying the trader nothing,
  * claim setting trader_fee_usdt = payout_fee_percent × amount with ZERO money,
  * freeze insufficient / exact-boundary terminal WORK,
  * trader_fee > commission (negative platform margin) settle behaviour,
  * terminal ESCROW starvation on settle (Insufficient funds, no partial move),
  * SEQUENTIAL double-settle / double-refund / double-claim idempotency.

Locks (SELECT…FOR UPDATE / advisory) are no-ops on SQLite, so concurrency is NOT
asserted here — only exact Decimal balances, ledger-leg counts, value
conservation, and sequential double-op safety.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.common.enums.users import UserRole
from app.core.exceptions import ForbiddenException, ValidationException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.payouts.exceptions import PayoutConflict
from app.modules.payouts.models import Payout, PayoutTerminal, payout_terminal_traders
from app.modules.payouts.service import PayoutService
from app.modules.traders.models import Trader
from app.modules.users.models import User

AMOUNT = Decimal("100.0000")        # amount_usdt sent to the end-user
MERCHANT_FEE = Decimal("5.0000")    # commission charged to the merchant
TRADER_FEE = Decimal("2.0000")      # trader reward
FREEZE = AMOUNT + MERCHANT_FEE      # 105 — frozen at creation


@pytest.fixture(autouse=True)
def _stub_celery():
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


# ── builders (local, self-contained — mirror test_payout_money_flow.py) ──


async def _mk_user(session, *, username, role=UserRole.TRADER) -> User:
    u = User(
        username=username, password=get_password_hash("pass12345"), role=role,
        totp_enabled=False, is_blocked=False, use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_trader_profile(session, *, user_id, fee_pct="2.00", is_active=True, hold_hours=0) -> Trader:
    t = Trader(user_id=user_id, is_payout_active=is_active,
               payout_fee_percent=Decimal(fee_pct), payout_hold_hours=hold_hours)
    session.add(t)
    await session.flush()
    return t


async def _mk_terminal(session, *, owner_id, suffix) -> PayoutTerminal:
    t = PayoutTerminal(
        user_id=owner_id, name=f"PT-{suffix}", status=TerminalStatus.ENABLED,
        currency=Currency.RUB, api_key=f"pk-{suffix}", api_secret=f"ps-{suffix}",
        commission_percent=Decimal("5.00"), ttl_minutes=60, receipts_to_close=1,
    )
    session.add(t)
    await session.flush()
    return t


async def _mk_balance(session, amount, *, user_id=None, payout_terminal_id=None,
                      is_system=False, btype=BalanceType.WORK) -> Balance:
    b = Balance(user_id=user_id, payout_terminal_id=payout_terminal_id, is_system=is_system,
                type=btype, currency=Currency.USDT, amount=Decimal(amount))
    session.add(b)
    await session.flush()
    return b


async def _mk_payout(session, *, terminal_id, trader_id=None, status=PayoutStatus.CREATED,
                     amount_usdt=AMOUNT, merchant_fee=MERCHANT_FEE, trader_fee=TRADER_FEE) -> Payout:
    p = Payout(
        uuid=uuid4(), external_id=f"ext-{uuid4().hex[:8]}", payout_terminal_id=terminal_id,
        trader_id=trader_id, payment_method=PaymentMethod.SBP,
        amount=Decimal("1000.00"), currency=Currency.RUB, exchange_rate=Decimal("10"),
        amount_usdt=amount_usdt, merchant_fee_usdt=merchant_fee,
        trader_fee_usdt=(trader_fee if trader_id else None),
        req_holder="End User", req_number="40817810099910000001", status=status,
    )
    session.add(p)
    await session.flush()
    return p


async def _bal(session, *, user_id=None, payout_terminal_id=None, is_system=False,
               btype=BalanceType.WORK) -> Decimal:
    stmt = select(Balance).where(Balance.type == btype, Balance.currency == Currency.USDT)
    if is_system:
        stmt = stmt.where(Balance.is_system.is_(True))
    elif user_id is not None:
        stmt = stmt.where(Balance.user_id == user_id)
    elif payout_terminal_id is not None:
        stmt = stmt.where(Balance.payout_terminal_id == payout_terminal_id)
    row = (await session.execute(stmt)).scalars().first()
    return row.amount if row else Decimal("0")


async def _legs(session, payout_id, *, reference_type=None) -> int:
    """Count ledger entries for this payout (optionally filtered by reference_type)."""
    stmt = select(func.count()).select_from(LedgerEntry).where(
        LedgerEntry.reference_id == f"payout:{payout_id}"
    )
    if reference_type is not None:
        stmt = stmt.where(LedgerEntry.reference_type == reference_type)
    return (await session.execute(stmt)).scalar_one()


async def _bind_trader(session, *, terminal_id, trader_user_id) -> None:
    """Add the trader (by USER id) to the terminal ACL so claim() is permitted."""
    await session.execute(
        payout_terminal_traders.insert().values(
            payout_terminal_id=terminal_id, trader_id=trader_user_id,
        )
    )
    await session.flush()


async def _setup(session, *, payout_status=PayoutStatus.CREATED, with_trader=True,
                 fee_pct="2.00", trader_active=True, hold_hours=0,
                 terminal_work=Decimal("0"), terminal_escrow=Decimal("0"),
                 merchant_fee=MERCHANT_FEE, trader_fee=TRADER_FEE, amount_usdt=AMOUNT,
                 assign_trader=True):
    """Build a terminal (optionally funded), an optional trader user+profile, and a
    payout. ``assign_trader=False`` leaves the payout UNassigned (pool state) even
    when a trader user exists — used for the claim tests, which assign on claim."""
    owner = await _mk_user(session, username=f"ow_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    terminal = await _mk_terminal(session, owner_id=owner.id, suffix=uuid4().hex[:6])
    if terminal_work:
        await _mk_balance(session, terminal_work, payout_terminal_id=terminal.id)
    if terminal_escrow:
        await _mk_balance(session, terminal_escrow, payout_terminal_id=terminal.id,
                          btype=BalanceType.ESCROW)
    trader = None
    if with_trader:
        trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
        await _mk_trader_profile(session, user_id=trader.id, fee_pct=fee_pct,
                                 is_active=trader_active, hold_hours=hold_hours)
    bound_trader_id = (trader.id if (trader and assign_trader) else None)
    payout = await _mk_payout(session, terminal_id=terminal.id,
                              trader_id=bound_trader_id, status=payout_status,
                              amount_usdt=amount_usdt, merchant_fee=merchant_fee,
                              trader_fee=trader_fee)
    return terminal, trader, payout


# ══════════════════════════════════════════════════════════════════════════
# CORRECT behaviour — leg shapes & conservation
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_settle_no_hold_leg_shape(session):
    """COMPLETED → exactly 3 legs under payout:{id}: 1 ORDER_PAYOUT (amount),
    1 SYSTEM_COMMISSION (merchant_fee), 1 TRADER_REWARD (trader_fee). System
    nets commission − trader_fee; value is conserved (105 in → 102 + 3 out)."""
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=FREEZE,
    )
    await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)

    assert await _legs(session, payout.id, reference_type=LedgerReferenceType.ORDER_PAYOUT) == 1
    assert await _legs(session, payout.id, reference_type=LedgerReferenceType.SYSTEM_COMMISSION) == 1
    assert await _legs(session, payout.id, reference_type=LedgerReferenceType.TRADER_REWARD) == 1
    assert await _legs(session, payout.id) == 3

    assert await _bal(session, user_id=trader.id) == AMOUNT + TRADER_FEE       # 102
    assert await _bal(session, is_system=True) == MERCHANT_FEE - TRADER_FEE    # 3
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")
    # Conservation: 105 frozen-out of ESCROW == 102 (trader) + 3 (system).
    assert (AMOUNT + TRADER_FEE) + (MERCHANT_FEE - TRADER_FEE) == FREEZE


@pytest.mark.asyncio
async def test_settle_merchant_fee_zero_omits_commission_leg(session):
    """merchant_fee=0 → no SYSTEM_COMMISSION leg. With the system pre-funded so the
    reward can be paid, settle has exactly ORDER_PAYOUT + TRADER_REWARD legs and
    the system balance drops by the trader_fee it pays out."""
    # Frozen escrow == amount only (merchant_fee=0 ⇒ only AMOUNT is frozen).
    # Pre-fund the system so the system→trader reward leg can succeed.
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=AMOUNT,
        merchant_fee=Decimal("0.0000"), trader_fee=TRADER_FEE,
    )
    await _mk_balance(session, Decimal("50"), is_system=True)
    await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)

    assert await _legs(session, payout.id, reference_type=LedgerReferenceType.SYSTEM_COMMISSION) == 0
    assert await _legs(session, payout.id, reference_type=LedgerReferenceType.ORDER_PAYOUT) == 1
    assert await _legs(session, payout.id, reference_type=LedgerReferenceType.TRADER_REWARD) == 1
    assert await _legs(session, payout.id) == 2

    assert await _bal(session, user_id=trader.id) == AMOUNT + TRADER_FEE       # 102
    # System received no commission, paid trader_fee out → 50 − 2.
    assert await _bal(session, is_system=True) == Decimal("50") - TRADER_FEE   # 48
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")


@pytest.mark.asyncio
async def test_settle_merchant_fee_zero_unfunded_system_raises_insufficient(session):
    """APP BEHAVIOUR PIN: merchant_fee=0 but trader_fee>0 with NO pre-existing
    system balance — the missing commission leg leaves the system at 0, so the
    system→trader reward raises Insufficient funds and the WHOLE settle rolls back
    (the trader is NOT reimbursed the amount either). A merchant_fee=0 payout that
    still owes a trader fee is effectively un-settleable unless the platform is
    pre-funded. Pinning current behaviour, not asserting it's desirable."""
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=AMOUNT,
        merchant_fee=Decimal("0.0000"), trader_fee=TRADER_FEE,
    )
    with pytest.raises(ValidationException, match="Insufficient funds"):
        await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)

    # Full rollback: escrow intact, nothing credited, still CLAIMED.
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == AMOUNT
    assert await _bal(session, user_id=trader.id) == Decimal("0")
    assert await _legs(session, payout.id) == 0
    refreshed = await session.get(Payout, payout.id)
    assert refreshed.status == PayoutStatus.CLAIMED


@pytest.mark.asyncio
async def test_settle_trader_fee_zero_omits_reward_leg_system_keeps_full_commission(session):
    """trader_fee=0 → no TRADER_REWARD leg; system keeps the FULL commission and
    the trader is reimbursed the amount only."""
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=FREEZE,
        merchant_fee=MERCHANT_FEE, trader_fee=Decimal("0.0000"),
    )
    await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)

    assert await _legs(session, payout.id, reference_type=LedgerReferenceType.TRADER_REWARD) == 0
    assert await _legs(session, payout.id, reference_type=LedgerReferenceType.ORDER_PAYOUT) == 1
    assert await _legs(session, payout.id, reference_type=LedgerReferenceType.SYSTEM_COMMISSION) == 1
    assert await _legs(session, payout.id) == 2

    assert await _bal(session, user_id=trader.id) == AMOUNT                    # 100, no reward
    assert await _bal(session, is_system=True) == MERCHANT_FEE                 # full 5
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")
    # Conservation: 105 out of ESCROW == 100 (trader) + 5 (system).
    assert AMOUNT + MERCHANT_FEE == FREEZE


@pytest.mark.asyncio
async def test_refund_leg_shape_escrow_to_work_amount_plus_commission(session):
    """CANCELED → exactly 1 ORDER_PAYOUT leg moving ESCROW → WORK for
    amount + commission (the full frozen total). No other legs."""
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CREATED, terminal_escrow=FREEZE,
    )
    await PayoutService(session).change_status(payout, PayoutStatus.CANCELED)

    assert await _legs(session, payout.id, reference_type=LedgerReferenceType.ORDER_PAYOUT) == 1
    assert await _legs(session, payout.id) == 1
    assert await _bal(session, payout_terminal_id=terminal.id) == FREEZE               # back in WORK
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")


@pytest.mark.asyncio
async def test_refund_from_claimed_pays_trader_nothing(session):
    """A CANCELED payout that was already CLAIMED still refunds the TERMINAL only —
    the trader (who never delivered) receives nothing."""
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=FREEZE,
    )
    await PayoutService(session).change_status(payout, PayoutStatus.CANCELED)

    assert await _bal(session, payout_terminal_id=terminal.id) == FREEZE
    assert await _bal(session, user_id=trader.id) == Decimal("0")
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")
    assert await _legs(session, payout.id, reference_type=LedgerReferenceType.TRADER_REWARD) == 0


@pytest.mark.asyncio
async def test_claim_sets_trader_fee_with_zero_money(session):
    """claim() computes trader_fee_usdt = payout_fee_percent × amount_usdt and
    moves NO money (escrow untouched, no trader balance created)."""
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CREATED, fee_pct="3.00",
        terminal_escrow=FREEZE, trader_fee=None, assign_trader=False,
    )
    await _bind_trader(session, terminal_id=terminal.id, trader_user_id=trader.id)

    claimed = await PayoutService(session).claim_payout(str(payout.uuid), trader)

    assert claimed.status == PayoutStatus.CLAIMED
    assert claimed.trader_id == trader.id
    # 3% of amount_usdt(100) = 3.0000.
    assert claimed.trader_fee_usdt == Decimal("3.0000")
    # Zero money moved by the claim.
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == FREEZE
    assert await _bal(session, user_id=trader.id) == Decimal("0")
    assert await _legs(session, payout.id) == 0


# ══════════════════════════════════════════════════════════════════════════
# ADVERSARIAL — freeze edges
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_freeze_insufficient_terminal_work_raises_and_moves_nothing(session):
    """Terminal WORK < amount+commission → ValidationException; no ESCROW created,
    WORK untouched, no ledger leg."""
    terminal, _t, payout = await _setup(
        session, with_trader=False, terminal_work=FREEZE - Decimal("0.0001"),
    )
    with pytest.raises(ValidationException, match="Insufficient payout terminal balance"):
        await PayoutService(session).finance.freeze_payout(payout, terminal)

    assert await _bal(session, payout_terminal_id=terminal.id) == FREEZE - Decimal("0.0001")
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _legs(session, payout.id) == 0


@pytest.mark.asyncio
async def test_freeze_exact_boundary_work_equals_total_ok(session):
    """WORK == total freezes cleanly to 0 WORK / total ESCROW (exact boundary)."""
    terminal, _t, payout = await _setup(session, with_trader=False, terminal_work=FREEZE)
    await PayoutService(session).finance.freeze_payout(payout, terminal)

    assert await _bal(session, payout_terminal_id=terminal.id) == Decimal("0")
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == FREEZE
    assert await _legs(session, payout.id, reference_type=LedgerReferenceType.ORDER_PAYOUT) == 1


@pytest.mark.asyncio
async def test_freeze_one_satoshi_below_total_raises(session):
    """WORK == total − 0.0001 raises — the boundary is strict (<, not <=)."""
    terminal, _t, payout = await _setup(
        session, with_trader=False, terminal_work=FREEZE - Decimal("0.0001"),
    )
    with pytest.raises(ValidationException, match="Insufficient payout terminal balance"):
        await PayoutService(session).finance.freeze_payout(payout, terminal)
    # Untouched.
    assert await _bal(session, payout_terminal_id=terminal.id) == FREEZE - Decimal("0.0001")


# ══════════════════════════════════════════════════════════════════════════
# ADVERSARIAL — settle edges
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_settle_trader_fee_exceeds_commission_unfunded_system_rolls_back(session):
    """APP BEHAVIOUR PIN: trader_fee(8) > commission(5) with no pre-existing system
    balance. The commission leg funds the system to 5, then the reward leg tries to
    pay 8 OUT → Insufficient funds. The transfer does NOT clamp / go negative — the
    whole settle rolls back: trader gets nothing, escrow intact, status unchanged.
    (So the platform can never be driven negative on a fresh system balance — over-
    reward just fails loudly. Pinning current behaviour.)"""
    big_fee = Decimal("8.0000")
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=FREEZE,
        merchant_fee=MERCHANT_FEE, trader_fee=big_fee,
    )
    with pytest.raises(ValidationException, match="Insufficient funds"):
        await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)

    assert await _bal(session, user_id=trader.id) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == FREEZE
    assert await _legs(session, payout.id) == 0
    refreshed = await session.get(Payout, payout.id)
    assert refreshed.status == PayoutStatus.CLAIMED


@pytest.mark.asyncio
async def test_settle_trader_fee_exceeds_commission_prefunded_system_drops(session):
    """trader_fee(8) > commission(5) WITH a pre-funded system: settle succeeds, the
    trader gets amount+8=108, and the system NETS commission−fee = −3 against its
    starting balance (50 → 47). Platform margin can be negative per-payout when the
    trader fee outweighs the commission — pin the exact arithmetic."""
    big_fee = Decimal("8.0000")
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=FREEZE,
        merchant_fee=MERCHANT_FEE, trader_fee=big_fee,
    )
    await _mk_balance(session, Decimal("50"), is_system=True)
    await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)

    assert await _bal(session, user_id=trader.id) == AMOUNT + big_fee                 # 108
    # System: 50 + 5 (commission) − 8 (reward) = 47  (net −3 on this payout).
    assert await _bal(session, is_system=True) == Decimal("50") + MERCHANT_FEE - big_fee
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _legs(session, payout.id) == 3


@pytest.mark.asyncio
async def test_settle_escrow_starvation_raises_insufficient_no_partial(session):
    """Terminal ESCROW under-funded for the amount leg → Insufficient funds; the
    whole transition rolls back (no trader credit, no partial commission)."""
    short_escrow = AMOUNT - Decimal("0.0001")   # can't even cover the amount leg
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=short_escrow,
    )
    with pytest.raises(ValidationException, match="Insufficient funds"):
        await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)

    # Nested-tx rollback: ESCROW intact, NOTHING credited anywhere, status unchanged.
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == short_escrow
    assert await _bal(session, user_id=trader.id) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")
    assert await _legs(session, payout.id) == 0
    refreshed = await session.get(Payout, payout.id)
    assert refreshed.status == PayoutStatus.CLAIMED


@pytest.mark.asyncio
async def test_settle_escrow_covers_amount_but_not_commission_rolls_back(session):
    """ESCROW covers the amount leg but NOT the commission leg → the commission
    transfer raises Insufficient funds and the whole nested tx rolls back: the
    trader does NOT keep the amount (no partial settlement)."""
    short_escrow = AMOUNT + MERCHANT_FEE - Decimal("0.0001")  # amount ok, commission short
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=short_escrow,
    )
    with pytest.raises(ValidationException, match="Insufficient funds"):
        await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)

    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == short_escrow
    assert await _bal(session, user_id=trader.id) == Decimal("0")
    assert await _legs(session, payout.id) == 0
    refreshed = await session.get(Payout, payout.id)
    assert refreshed.status == PayoutStatus.CLAIMED


# ══════════════════════════════════════════════════════════════════════════
# ADVERSARIAL — double-op idempotency (SEQUENTIAL; locks are SQLite no-ops)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_double_settle_moves_money_once(session):
    """Calling COMPLETE twice: the 2nd is a same-status no-op (early-out). Money
    moved EXACTLY once — balances and leg counts unchanged after the 2nd call."""
    svc = PayoutService(session)
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=FREEZE,
    )
    await svc.change_status(payout, PayoutStatus.COMPLETED)

    trader_after_1 = await _bal(session, user_id=trader.id)
    system_after_1 = await _bal(session, is_system=True)
    legs_after_1 = await _legs(session, payout.id)

    # Second COMPLETED on an already-COMPLETED payout → no-op.
    refreshed = await session.get(Payout, payout.id)
    await svc.change_status(refreshed, PayoutStatus.COMPLETED)

    assert await _bal(session, user_id=trader.id) == trader_after_1 == AMOUNT + TRADER_FEE
    assert await _bal(session, is_system=True) == system_after_1 == MERCHANT_FEE - TRADER_FEE
    assert await _legs(session, payout.id) == legs_after_1 == 3
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")


@pytest.mark.asyncio
async def test_double_refund_moves_money_once(session):
    """Calling CANCEL twice: 2nd is a same-status no-op. Refund happens once."""
    svc = PayoutService(session)
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CREATED, terminal_escrow=FREEZE,
    )
    await svc.change_status(payout, PayoutStatus.CANCELED)

    work_after_1 = await _bal(session, payout_terminal_id=terminal.id)
    legs_after_1 = await _legs(session, payout.id)

    refreshed = await session.get(Payout, payout.id)
    await svc.change_status(refreshed, PayoutStatus.CANCELED)

    assert await _bal(session, payout_terminal_id=terminal.id) == work_after_1 == FREEZE
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _legs(session, payout.id) == legs_after_1 == 1


@pytest.mark.asyncio
async def test_settle_then_refund_is_illegal_transition_money_unchanged(session):
    """A COMPLETED payout cannot be refunded (CANCELED) — terminal state.
    PayoutConflict; the settled money is untouched."""
    svc = PayoutService(session)
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=FREEZE,
    )
    await svc.change_status(payout, PayoutStatus.COMPLETED)
    trader_bal = await _bal(session, user_id=trader.id)
    legs = await _legs(session, payout.id)

    refreshed = await session.get(Payout, payout.id)
    with pytest.raises(PayoutConflict, match="Illegal payout transition"):
        await svc.change_status(refreshed, PayoutStatus.CANCELED)

    # No refund leg appeared; trader still holds the settled earnings.
    assert await _bal(session, user_id=trader.id) == trader_bal == AMOUNT + TRADER_FEE
    assert await _legs(session, payout.id) == legs == 3
    assert await _bal(session, payout_terminal_id=terminal.id) == Decimal("0")


@pytest.mark.asyncio
async def test_double_claim_conflict_first_trader_fee_not_overwritten(session):
    """A second claim of a CLAIMED payout raises PayoutConflict; the first
    trader's fee/assignment is NOT overwritten and no money moves."""
    svc = PayoutService(session)
    terminal, trader1, payout = await _setup(
        session, payout_status=PayoutStatus.CREATED, fee_pct="3.00",
        terminal_escrow=FREEZE, trader_fee=None, assign_trader=False,
    )
    await _bind_trader(session, terminal_id=terminal.id, trader_user_id=trader1.id)

    # Second trader (different fee %) also bound to the terminal.
    trader2 = await _mk_user(session, username=f"tr2_{uuid4().hex[:6]}")
    await _mk_trader_profile(session, user_id=trader2.id, fee_pct="9.00")
    await _bind_trader(session, terminal_id=terminal.id, trader_user_id=trader2.id)

    claimed = await svc.claim_payout(str(payout.uuid), trader1)
    assert claimed.trader_id == trader1.id
    assert claimed.trader_fee_usdt == Decimal("3.0000")

    with pytest.raises(PayoutConflict):
        await svc.claim_payout(str(payout.uuid), trader2)

    refreshed = await session.get(Payout, payout.id)
    assert refreshed.trader_id == trader1.id                      # first claimer kept
    assert refreshed.trader_fee_usdt == Decimal("3.0000")         # NOT overwritten to 9%
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == FREEZE
    assert await _legs(session, payout.id) == 0


@pytest.mark.asyncio
async def test_claim_by_disabled_trader_forbidden_no_money(session):
    """A trader with is_payout_active=False is rejected at claim with Forbidden;
    no status change, no money."""
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CREATED, trader_active=False,
        terminal_escrow=FREEZE, trader_fee=None, assign_trader=False,
    )
    await _bind_trader(session, terminal_id=terminal.id, trader_user_id=trader.id)

    with pytest.raises(ForbiddenException, match="Payouts are not enabled"):
        await PayoutService(session).claim_payout(str(payout.uuid), trader)

    refreshed = await session.get(Payout, payout.id)
    assert refreshed.status == PayoutStatus.CREATED
    assert refreshed.trader_id is None
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == FREEZE
    assert await _legs(session, payout.id) == 0


@pytest.mark.asyncio
async def test_claim_by_trader_not_on_terminal_acl_forbidden(session):
    """An active trader NOT bound to the terminal's ACL is rejected with Forbidden
    (assigned-to-terminal guard) — no money, no claim."""
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CREATED, terminal_escrow=FREEZE,
        trader_fee=None, assign_trader=False,
    )
    # Intentionally do NOT bind the trader to the terminal ACL.

    with pytest.raises(ForbiddenException, match="not assigned to this payout terminal"):
        await PayoutService(session).claim_payout(str(payout.uuid), trader)

    refreshed = await session.get(Payout, payout.id)
    assert refreshed.status == PayoutStatus.CREATED
    assert refreshed.trader_id is None
    assert await _legs(session, payout.id) == 0
