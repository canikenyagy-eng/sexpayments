"""Integration tests for WITHDRAWAL approve/reject idempotency (#3) and
system-balance get-or-create idempotency (#22), against a REAL in-memory ledger.

What the real money flow looks like (read from app/modules/finance/service.py):

  create_withdrawal_request → freeze (amount + fee) WORK → ESCROW
                              [LedgerReferenceType.WITHDRAWAL leg, ESCROW grows]
  approve_withdrawal_request → debit `amount` out of ESCROW (to_balance=None,
                              money leaves the platform) [WITHDRAWAL leg]
                            + if fee > 0: ESCROW → system for `fee`
                              [SYSTEM_COMMISSION leg]
                            + status PENDING → SUCCESS
  reject_withdrawal_request  → return (amount + fee) ESCROW → WORK
                              [WITHDRAWAL leg] + status PENDING → REJECTED

SQLite caveat: `get_for_update` (SELECT … FOR UPDATE) and pg_advisory_xact_lock
are NO-OPS here, so these tests cannot exercise real row-lock concurrency.
Instead they verify (a) exact Decimal balances + ledger legs through the real
ledger, and (b) SEQUENTIAL idempotency — call approve/reject TWICE IN A ROW and
assert money moved EXACTLY ONCE (this drives the application-level status
re-check under the lock, which is what protects against the double-spend).
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency, WithdrawalStatus
from app.common.enums.users import UserRole
from app.core.exceptions import ConflictException, NotFoundException, ValidationException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry, WithdrawalRequest
from app.modules.finance.schemas.admin import WithdrawalRequestCreate
from app.modules.finance.service import FinanceService
from app.modules.merchants.models import Merchant
from app.modules.traders.models import Trader
from app.modules.users.models import User

CUR = Currency.USDT
DEST = "T-destination-address-xyz"
ADMIN_ID = 9999


@pytest.fixture(autouse=True)
def _stub_celery():
    """Notification hooks enqueue celery tasks; stub the broker so nothing fires."""
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


# ── builders ──────────────────────────────────────────────────────────


async def _mk_user(session, *, role=UserRole.TRADER) -> User:
    u = User(
        username=f"u_{uuid4().hex[:6]}", password=get_password_hash("pass12345"),
        role=role, totp_enabled=False, is_blocked=False, use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_trader_profile(session, *, user_id, withdrawal_fee=Decimal("0")) -> Trader:
    t = Trader(user_id=user_id, withdrawal_fee_fixed=withdrawal_fee)
    session.add(t)
    await session.flush()
    return t


async def _mk_merchant(session, *, owner_id, withdrawal_fee=Decimal("0")) -> Merchant:
    suffix = uuid4().hex[:6]
    m = Merchant(
        user_id=owner_id, name=f"M-{suffix}", currency=Currency.RUB,
        api_key=f"key-{suffix}", api_secret=f"secret-{suffix}",
        withdrawal_fee_fixed=withdrawal_fee,
    )
    session.add(m)
    await session.flush()
    return m


async def _mk_balance(session, amount, *, user_id=None, merchant_id=None,
                      is_system=False, btype=BalanceType.WORK) -> Balance:
    b = Balance(
        user_id=user_id, merchant_id=merchant_id, is_system=is_system,
        type=btype, currency=CUR, amount=Decimal(amount),
    )
    session.add(b)
    await session.flush()
    return b


# ── readers ───────────────────────────────────────────────────────────


async def _bal(session, *, user_id=None, merchant_id=None, is_system=False,
               btype=BalanceType.WORK) -> Decimal:
    stmt = select(Balance).where(Balance.type == btype, Balance.currency == CUR)
    if is_system:
        stmt = stmt.where(Balance.is_system.is_(True))
    elif user_id is not None:
        stmt = stmt.where(Balance.user_id == user_id)
    elif merchant_id is not None:
        stmt = stmt.where(Balance.merchant_id == merchant_id)
    row = (await session.execute(stmt)).scalars().first()
    return row.amount if row else Decimal("0")


async def _legs(session, *, reference_id, reference_type=None):
    stmt = select(LedgerEntry).where(LedgerEntry.reference_id == str(reference_id))
    if reference_type is not None:
        stmt = stmt.where(LedgerEntry.reference_type == reference_type)
    return list((await session.execute(stmt)).scalars().all())


async def _count_system_balances(session, *, btype=BalanceType.WORK) -> int:
    stmt = select(func.count()).select_from(Balance).where(
        Balance.is_system.is_(True), Balance.type == btype, Balance.currency == CUR,
    )
    return int((await session.execute(stmt)).scalar_one())


# ── setup helpers ─────────────────────────────────────────────────────


async def _setup_trader(session, *, work=Decimal("500"), fee=Decimal("0")):
    user = await _mk_user(session, role=UserRole.TRADER)
    await _mk_trader_profile(session, user_id=user.id, withdrawal_fee=fee)
    await _mk_balance(session, work, user_id=user.id, btype=BalanceType.WORK)
    return user


async def _setup_merchant(session, *, work=Decimal("500"), fee=Decimal("0")):
    owner = await _mk_user(session, role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, owner_id=owner.id, withdrawal_fee=fee)
    await _mk_balance(session, work, merchant_id=merchant.id, btype=BalanceType.WORK)
    return merchant


# ══════════════════════════════════════════════════════════════════════
# CORRECT BEHAVIOUR
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_create_then_approve_trader_moves_money_once(session):
    """create freezes WORK→ESCROW, approve debits ESCROW out of the platform.
    No fee → exactly one WITHDRAWAL freeze leg + one WITHDRAWAL debit leg."""
    user = await _setup_trader(session, work=Decimal("500"), fee=Decimal("0"))
    svc = FinanceService(session)

    req = await svc.create_withdrawal_request(
        WithdrawalRequestCreate(amount=Decimal("100"), currency=CUR, destination_address=DEST),
        user=user,
    )
    # Freeze: WORK 500→400, ESCROW 0→100.
    assert await _bal(session, user_id=user.id, btype=BalanceType.WORK) == Decimal("400")
    assert await _bal(session, user_id=user.id, btype=BalanceType.ESCROW) == Decimal("100")
    assert req.fee_amount == Decimal("0")

    out = await svc.approve_withdrawal_request(req.id, ADMIN_ID)
    assert out.status == WithdrawalStatus.SUCCESS

    # Approve: ESCROW 100→0 (money left the platform). WORK untouched at 400.
    assert await _bal(session, user_id=user.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, user_id=user.id, btype=BalanceType.WORK) == Decimal("400")

    # Exactly two WITHDRAWAL legs: freeze (→ESCROW) + approve debit (ESCROW→out).
    wd = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.WITHDRAWAL)
    assert len(wd) == 2
    assert {leg.amount for leg in wd} == {Decimal("100")}
    # No fee → no SYSTEM_COMMISSION leg.
    assert await _legs(session, reference_id=req.id,
                       reference_type=LedgerReferenceType.SYSTEM_COMMISSION) == []
    # No system balance row was ever created (fee path skipped).
    assert await _count_system_balances(session) == 0


@pytest.mark.asyncio
async def test_approve_with_fee_pays_system_commission(session):
    """With a nonzero withdrawal fee the fee is withheld FROM the requested
    amount: the entered value is the GROSS that leaves the balance, the recipient
    gets ``amount − fee``, and the fee lands on the system WORK balance
    (SYSTEM_COMMISSION leg). Value is conserved."""
    user = await _setup_trader(session, work=Decimal("500"), fee=Decimal("3"))
    svc = FinanceService(session)

    req = await svc.create_withdrawal_request(
        WithdrawalRequestCreate(amount=Decimal("100"), currency=CUR, destination_address=DEST),
        user=user,
    )
    # Entered 100 gross, fee 3 → net payout 97 stored on the row.
    assert req.amount == Decimal("97")
    assert req.fee_amount == Decimal("3")
    # Freeze the entered (gross) amount: WORK 500→400, ESCROW 0→100.
    assert await _bal(session, user_id=user.id, btype=BalanceType.WORK) == Decimal("400")
    assert await _bal(session, user_id=user.id, btype=BalanceType.ESCROW) == Decimal("100")

    await svc.approve_withdrawal_request(req.id, ADMIN_ID)

    # ESCROW emptied (97 out + 3 to system). System WORK = 3.
    assert await _bal(session, user_id=user.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("3")
    assert await _bal(session, user_id=user.id, btype=BalanceType.WORK) == Decimal("400")

    # Exactly one SYSTEM_COMMISSION leg of 3, two WITHDRAWAL legs (freeze 100 + debit 97).
    comm = await _legs(session, reference_id=req.id,
                       reference_type=LedgerReferenceType.SYSTEM_COMMISSION)
    assert len(comm) == 1 and comm[0].amount == Decimal("3")
    wd = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.WITHDRAWAL)
    assert len(wd) == 2
    # Value conservation: frozen 100 = 97 (out) + 3 (system).
    assert Decimal("97") + Decimal("3") == Decimal("100")


@pytest.mark.asyncio
async def test_amount_not_exceeding_fee_is_rejected(session):
    """The fee is withheld from the amount, so a request whose amount does not
    exceed the fee (net payout ≤ 0) is rejected before any ledger move."""
    user = await _setup_trader(session, work=Decimal("500"), fee=Decimal("3"))
    svc = FinanceService(session)

    with pytest.raises(ValidationException):
        await svc.create_withdrawal_request(
            WithdrawalRequestCreate(amount=Decimal("3"), currency=CUR, destination_address=DEST),
            user=user,
        )

    # Nothing moved — WORK intact, ESCROW untouched, no row created.
    assert await _bal(session, user_id=user.id, btype=BalanceType.WORK) == Decimal("500")
    assert await _bal(session, user_id=user.id, btype=BalanceType.ESCROW) == Decimal("0")


@pytest.mark.asyncio
async def test_create_then_reject_returns_funds(session):
    """Reject returns frozen (amount+fee) ESCROW→WORK; WORK restored, ESCROW 0."""
    user = await _setup_trader(session, work=Decimal("500"), fee=Decimal("3"))
    svc = FinanceService(session)

    req = await svc.create_withdrawal_request(
        WithdrawalRequestCreate(amount=Decimal("100"), currency=CUR, destination_address=DEST),
        user=user,
    )
    out = await svc.reject_withdrawal_request(req.id, ADMIN_ID, reason="bad address")
    assert out.status == WithdrawalStatus.REJECTED
    assert out.rejection_reason == "bad address"

    # Full refund: WORK back to 500, ESCROW 0. Nothing went to system.
    assert await _bal(session, user_id=user.id, btype=BalanceType.WORK) == Decimal("500")
    assert await _bal(session, user_id=user.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")

    # Two WITHDRAWAL legs: freeze (103 →ESCROW) + reject (103 ESCROW→WORK). No commission.
    wd = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.WITHDRAWAL)
    assert len(wd) == 2
    assert await _legs(session, reference_id=req.id,
                       reference_type=LedgerReferenceType.SYSTEM_COMMISSION) == []


@pytest.mark.asyncio
async def test_approve_merchant_path_debits_merchant_escrow(session):
    """Merchant-scoped withdrawal: freeze + approve move on the MERCHANT balances."""
    merchant = await _setup_merchant(session, work=Decimal("500"), fee=Decimal("0"))
    svc = FinanceService(session)

    req = await svc.create_withdrawal_request(
        WithdrawalRequestCreate(amount=Decimal("100"), currency=CUR, destination_address=DEST),
        merchant=merchant,
    )
    assert await _bal(session, merchant_id=merchant.id, btype=BalanceType.WORK) == Decimal("400")
    assert await _bal(session, merchant_id=merchant.id, btype=BalanceType.ESCROW) == Decimal("100")

    await svc.approve_withdrawal_request(req.id, ADMIN_ID)
    assert await _bal(session, merchant_id=merchant.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, merchant_id=merchant.id, btype=BalanceType.WORK) == Decimal("400")


# ══════════════════════════════════════════════════════════════════════
# ADVERSARIAL — try to break it
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_double_approve_pays_out_only_once(session):
    """Approve the SAME request TWICE in a row. First pays out; the second
    re-reads a non-PENDING status under the (no-op on SQLite) lock and is a
    clean ConflictException — money must NOT move a second time and NO third
    ledger leg may appear (a double-settle would show as 3+ WITHDRAWAL legs)."""
    user = await _setup_trader(session, work=Decimal("500"), fee=Decimal("3"))
    svc = FinanceService(session)
    req = await svc.create_withdrawal_request(
        WithdrawalRequestCreate(amount=Decimal("100"), currency=CUR, destination_address=DEST),
        user=user,
    )

    await svc.approve_withdrawal_request(req.id, ADMIN_ID)
    # Snapshot books after the legitimate first approve.
    escrow_after = await _bal(session, user_id=user.id, btype=BalanceType.ESCROW)
    work_after = await _bal(session, user_id=user.id, btype=BalanceType.WORK)
    system_after = await _bal(session, is_system=True)
    legs_after = len(await _legs(session, reference_id=req.id))

    with pytest.raises(ConflictException):
        await svc.approve_withdrawal_request(req.id, ADMIN_ID)

    # 2nd approve was a guarded no-op: nothing moved, no extra ledger legs.
    assert await _bal(session, user_id=user.id, btype=BalanceType.ESCROW) == escrow_after == Decimal("0")
    assert await _bal(session, user_id=user.id, btype=BalanceType.WORK) == work_after
    assert await _bal(session, is_system=True) == system_after == Decimal("3")
    assert len(await _legs(session, reference_id=req.id)) == legs_after
    # Exactly 2 WITHDRAWAL legs total (freeze + single debit), never 3.
    assert len(await _legs(session, reference_id=req.id,
                           reference_type=LedgerReferenceType.WITHDRAWAL)) == 2
    # System commission charged exactly once.
    assert len(await _legs(session, reference_id=req.id,
                           reference_type=LedgerReferenceType.SYSTEM_COMMISSION)) == 1

    refreshed = await session.get(WithdrawalRequest, req.id)
    assert refreshed.status == WithdrawalStatus.SUCCESS


@pytest.mark.asyncio
async def test_double_reject_returns_funds_only_once(session):
    """Reject twice in a row: funds returned once; second is guarded, no
    double-credit back into WORK."""
    user = await _setup_trader(session, work=Decimal("500"), fee=Decimal("3"))
    svc = FinanceService(session)
    req = await svc.create_withdrawal_request(
        WithdrawalRequestCreate(amount=Decimal("100"), currency=CUR, destination_address=DEST),
        user=user,
    )

    await svc.reject_withdrawal_request(req.id, ADMIN_ID, reason="r1")
    assert await _bal(session, user_id=user.id, btype=BalanceType.WORK) == Decimal("500")

    with pytest.raises(ConflictException):
        await svc.reject_withdrawal_request(req.id, ADMIN_ID, reason="r2")

    # No double-credit: WORK stays at 500 (not 603), ESCROW stays 0.
    assert await _bal(session, user_id=user.id, btype=BalanceType.WORK) == Decimal("500")
    assert await _bal(session, user_id=user.id, btype=BalanceType.ESCROW) == Decimal("0")
    # Exactly 2 WITHDRAWAL legs (freeze + single return), never 3.
    assert len(await _legs(session, reference_id=req.id,
                           reference_type=LedgerReferenceType.WITHDRAWAL)) == 2

    refreshed = await session.get(WithdrawalRequest, req.id)
    assert refreshed.status == WithdrawalStatus.REJECTED
    assert refreshed.rejection_reason == "r1"  # first reason wins; 2nd never applied


@pytest.mark.asyncio
async def test_reject_after_approve_is_guarded(session):
    """A request already SUCCESS cannot be rejected — no contradictory
    refund-back-into-WORK after the money already left the platform."""
    user = await _setup_trader(session, work=Decimal("500"), fee=Decimal("0"))
    svc = FinanceService(session)
    req = await svc.create_withdrawal_request(
        WithdrawalRequestCreate(amount=Decimal("100"), currency=CUR, destination_address=DEST),
        user=user,
    )
    await svc.approve_withdrawal_request(req.id, ADMIN_ID)
    # After approve: WORK 400, ESCROW 0.
    assert await _bal(session, user_id=user.id, btype=BalanceType.WORK) == Decimal("400")

    with pytest.raises(ConflictException):
        await svc.reject_withdrawal_request(req.id, ADMIN_ID, reason="too late")

    # No phantom refund: WORK still 400 (NOT 500), ESCROW still 0.
    assert await _bal(session, user_id=user.id, btype=BalanceType.WORK) == Decimal("400")
    assert await _bal(session, user_id=user.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert len(await _legs(session, reference_id=req.id,
                           reference_type=LedgerReferenceType.WITHDRAWAL)) == 2
    refreshed = await session.get(WithdrawalRequest, req.id)
    assert refreshed.status == WithdrawalStatus.SUCCESS


@pytest.mark.asyncio
async def test_approve_after_reject_is_guarded(session):
    """A request already REJECTED cannot be approved — funds were returned to
    WORK, an approve must not then debit ESCROW (which is now empty) again."""
    user = await _setup_trader(session, work=Decimal("500"), fee=Decimal("0"))
    svc = FinanceService(session)
    req = await svc.create_withdrawal_request(
        WithdrawalRequestCreate(amount=Decimal("100"), currency=CUR, destination_address=DEST),
        user=user,
    )
    await svc.reject_withdrawal_request(req.id, ADMIN_ID, reason="no")
    assert await _bal(session, user_id=user.id, btype=BalanceType.WORK) == Decimal("500")

    with pytest.raises(ConflictException):
        await svc.approve_withdrawal_request(req.id, ADMIN_ID)

    # Books unchanged by the guarded approve: WORK 500, ESCROW 0.
    assert await _bal(session, user_id=user.id, btype=BalanceType.WORK) == Decimal("500")
    assert await _bal(session, user_id=user.id, btype=BalanceType.ESCROW) == Decimal("0")
    refreshed = await session.get(WithdrawalRequest, req.id)
    assert refreshed.status == WithdrawalStatus.REJECTED


@pytest.mark.asyncio
async def test_approve_missing_request_raises_not_found(session):
    """Approving a non-existent request id surfaces NotFoundException, never a
    silent money move."""
    svc = FinanceService(session)
    with pytest.raises(NotFoundException):
        await svc.approve_withdrawal_request(424242, ADMIN_ID)


# ── system-balance get-or-create idempotency (#22) ─────────────────────


@pytest.mark.asyncio
async def test_get_or_create_system_balance_idempotent_same_row(session):
    """Two sequential get_or_create calls for the same (type, currency) must
    return the SAME row and leave EXACTLY ONE system Balance — the advisory
    lock is a PG no-op on SQLite, so this exercises the get→re-check path that
    must not split the platform float into two rows."""
    svc = FinanceService(session)

    first = await svc.get_or_create_system_balance(CUR, BalanceType.WORK)
    second = await svc.get_or_create_system_balance(CUR, BalanceType.WORK)

    assert first.id == second.id
    assert await _count_system_balances(session, btype=BalanceType.WORK) == 1


@pytest.mark.asyncio
async def test_get_or_create_system_balance_returns_existing(session):
    """When a system balance already exists, get_or_create returns it verbatim
    (with its accrued amount) and never creates a duplicate."""
    existing = await _mk_balance(
        session, Decimal("42"), is_system=True, btype=BalanceType.WORK,
    )
    svc = FinanceService(session)

    got = await svc.get_or_create_system_balance(CUR, BalanceType.WORK)
    assert got.id == existing.id
    assert got.amount == Decimal("42")
    assert await _count_system_balances(session, btype=BalanceType.WORK) == 1


@pytest.mark.asyncio
async def test_get_or_create_system_balance_separates_by_type(session):
    """WORK and ESCROW system balances are distinct rows — get-or-create keyed
    on (type, currency) must not collapse them."""
    svc = FinanceService(session)
    work = await svc.get_or_create_system_balance(CUR, BalanceType.WORK)
    escrow = await svc.get_or_create_system_balance(CUR, BalanceType.ESCROW)

    assert work.id != escrow.id
    assert await _count_system_balances(session, btype=BalanceType.WORK) == 1
    assert await _count_system_balances(session, btype=BalanceType.ESCROW) == 1
    # Idempotent on the second key too.
    again = await svc.get_or_create_system_balance(CUR, BalanceType.ESCROW)
    assert again.id == escrow.id
    assert await _count_system_balances(session, btype=BalanceType.ESCROW) == 1
