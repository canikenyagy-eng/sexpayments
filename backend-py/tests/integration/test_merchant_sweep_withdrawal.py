"""Integration tests for the MERCHANT CROSS-TERMINAL SWEEP withdrawal money
flow against a REAL in-memory ledger.

`FinanceService.create_merchant_sweep_withdrawal` (app/modules/finance/service.py:587)
drains every terminal's WORK balance onto the merchant OWNER's user-level WORK
balance, then freezes the requested amount + summed per-terminal fees into the
owner's user-level ESCROW. The created `WithdrawalRequest` has `merchant_id = None`
(owner-scoped), so the admin approve/reject paths take their merchant-OWNER
branches (service.py:1015 / 1096) and move money on the OWNER's user balances.

Model (read from the code):

  create_merchant_sweep_withdrawal:
    * non-MERCHANT owner            → ValidationException (before any DB work)
    * no terminals                  → ValidationException
    * owner-WORK pre-drain: existing owner WORK reduces `remaining` WITHOUT any
      ledger leg (the freeze pulls it straight from owner WORK).
    * per terminal (FIFO by merchants.id), while remaining > 0:
        spendable = terminal.WORK - terminal.withdrawal_fee_fixed
        if spendable <= 0: terminal is SKIPPED (no leg, fee NOT counted)
        else: take = min(spendable, remaining)
              transfer take+fee  terminal WORK → owner WORK   [INTERNAL_TRANSFER]
              total_fee += fee
    * if remaining still > 0 after all terminals → ValidationException
      (NOTHING written: no withdrawal row, no ledger legs — the row is created
      and the legs move INSIDE a begin_nested AFTER the aggregate check).
    * freeze amount+total_fee  owner WORK → owner ESCROW         [WITHDRAWAL]
    * stored fee_amount == sum of ONLY participating terminals' fees.

  approve_withdrawal_request (merchant-owner branch, merchant_id is None):
    * owner ESCROW → out  for `amount`                          [WITHDRAWAL]
    * owner ESCROW → system  for `fee`                          [SYSTEM_COMMISSION]
  reject_withdrawal_request (merchant-owner branch):
    * owner ESCROW → owner WORK  for `amount + fee`             [WITHDRAWAL]

Reference-id invariant: every INTERNAL_TRANSFER sweep leg AND the WITHDRAWAL
freeze leg share `str(withdrawal_req.id)`. They remain distinguishable by
`reference_type`.

SQLite caveat: SELECT…FOR UPDATE / advisory locks are NO-OPS here, so these
tests verify exact Decimal balances, ledger-leg counts, value conservation, and
SEQUENTIAL idempotency (approve/reject twice in a row → money moves once).
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency, WithdrawalStatus
from app.common.enums.users import UserRole
from app.core.exceptions import ConflictException, ValidationException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry, WithdrawalRequest
from app.modules.finance.schemas.admin import WithdrawalRequestCreate
from app.modules.finance.service import FinanceService
from app.modules.merchants.models import Merchant
from app.modules.users.models import User

CUR = Currency.USDT
DEST = "T-destination-address-xyz"
ADMIN_ID = 9999


@pytest.fixture(autouse=True)
def _stub_celery():
    """Sweep + approve/reject enqueue celery notifications; stub the broker."""
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


# ── builders ──────────────────────────────────────────────────────────


async def _mk_user(session, *, role=UserRole.MERCHANT) -> User:
    u = User(
        username=f"u_{uuid4().hex[:6]}", password=get_password_hash("pass12345"),
        role=role, totp_enabled=False, is_blocked=False, use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_merchant(session, *, owner_id, fee=Decimal("0")) -> Merchant:
    """A payin terminal owned by `owner_id`. `withdrawal_fee_fixed` is the
    per-terminal fee charged when the terminal participates in a sweep."""
    suffix = uuid4().hex[:6]
    m = Merchant(
        user_id=owner_id, name=f"M-{suffix}", currency=Currency.RUB,
        api_key=f"key-{suffix}", api_secret=f"secret-{suffix}",
        withdrawal_fee_fixed=Decimal(fee),
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


async def _count_withdrawals(session) -> int:
    return int((await session.execute(
        select(func.count()).select_from(WithdrawalRequest)
    )).scalar_one())


async def _count_all_legs(session) -> int:
    return int((await session.execute(
        select(func.count()).select_from(LedgerEntry)
    )).scalar_one())


# ── setup ─────────────────────────────────────────────────────────────


async def _mk_owner_with_terminals(session, *, terminals, owner_work=Decimal("0")):
    """`terminals` is a list of (work, fee) tuples. Returns (owner, [Merchant])
    ordered by id (== insertion / FIFO drain order). Optionally seeds an
    owner-level WORK balance."""
    owner = await _mk_user(session, role=UserRole.MERCHANT)
    if owner_work:
        await _mk_balance(session, owner_work, user_id=owner.id, btype=BalanceType.WORK)
    ms = []
    for work, fee in terminals:
        m = await _mk_merchant(session, owner_id=owner.id, fee=fee)
        await _mk_balance(session, work, merchant_id=m.id, btype=BalanceType.WORK)
        ms.append(m)
    return owner, ms


def _create(amount):
    return WithdrawalRequestCreate(amount=Decimal(amount), currency=CUR, destination_address=DEST)


# ══════════════════════════════════════════════════════════════════════
# CORRECT BEHAVIOUR
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_multi_terminal_sweep_moves_each_terminal_to_owner_escrow(session):
    """Three terminals, all participate. Each contributes take+fee → owner WORK
    (N INTERNAL_TRANSFER legs), then amount+sum(fees) is frozen owner WORK→ESCROW
    (one WITHDRAWAL leg). Stored fee == sum of participating fees. Conservation."""
    # T1: work 100 fee 1 → spendable 99 ; T2: work 200 fee 2 → spendable 198 ;
    # T3: work  50 fee 1 → spendable 49.  Request 150 (< total spendable 346).
    owner, (t1, t2, t3) = await _mk_owner_with_terminals(
        session,
        terminals=[(Decimal("100"), Decimal("1")),
                   (Decimal("200"), Decimal("2")),
                   (Decimal("50"), Decimal("1"))],
    )
    svc = FinanceService(session)

    req = await svc.create_merchant_sweep_withdrawal(_create("150"), owner)

    # FIFO plan: T1 take 99 (remaining 150→51), T2 take 51 (remaining 0), T3 NOT
    # reached (remaining already 0 → break). Only T1+T2 participate.
    assert req.merchant_id is None
    assert req.user_id == owner.id
    assert req.user_role == UserRole.MERCHANT
    assert req.amount == Decimal("150")
    # Fee = T1.fee + T2.fee = 1 + 2 = 3 (T3 skipped → its fee NOT counted).
    assert req.fee_amount == Decimal("3")

    # Terminal WORK drained by take+fee: T1 100-(99+1)=0 ; T2 200-(51+2)=147 ;
    # T3 untouched = 50.
    assert await _bal(session, merchant_id=t1.id) == Decimal("0")
    assert await _bal(session, merchant_id=t2.id) == Decimal("147")
    assert await _bal(session, merchant_id=t3.id) == Decimal("50")

    # Owner WORK net 0 (received 99+1 + 51+2 = 153, froze 150+3 = 153).
    assert await _bal(session, user_id=owner.id, btype=BalanceType.WORK) == Decimal("0")
    # Owner ESCROW holds amount+fee = 153.
    assert await _bal(session, user_id=owner.id, btype=BalanceType.ESCROW) == Decimal("153")

    # Leg counts: 2 INTERNAL_TRANSFER (T1, T2) + 1 WITHDRAWAL freeze.
    it = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.INTERNAL_TRANSFER)
    assert len(it) == 2
    assert {leg.amount for leg in it} == {Decimal("100"), Decimal("53")}  # take+fee each
    wd = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.WITHDRAWAL)
    assert len(wd) == 1 and wd[0].amount == Decimal("153")

    # Conservation: money pulled off terminals (153) == frozen in owner ESCROW (153).
    assert Decimal("100") + Decimal("53") == Decimal("153")


@pytest.mark.asyncio
async def test_sweep_reference_id_shared_but_distinguishable_by_type(session):
    """All INTERNAL_TRANSFER sweep legs AND the WITHDRAWAL freeze leg share the
    SAME reference_id (str(req.id)); they're told apart only by reference_type."""
    owner, (t1, t2) = await _mk_owner_with_terminals(
        session,
        terminals=[(Decimal("100"), Decimal("0")), (Decimal("100"), Decimal("0"))],
    )
    svc = FinanceService(session)
    req = await svc.create_merchant_sweep_withdrawal(_create("150"), owner)

    all_legs = await _legs(session, reference_id=req.id)
    # 2 sweep + 1 freeze = 3 legs, every one keyed to str(req.id).
    assert len(all_legs) == 3
    assert {leg.reference_id for leg in all_legs} == {str(req.id)}
    by_type = {}
    for leg in all_legs:
        by_type.setdefault(leg.reference_type, []).append(leg)
    assert len(by_type[LedgerReferenceType.INTERNAL_TRANSFER]) == 2
    assert len(by_type[LedgerReferenceType.WITHDRAWAL]) == 1


@pytest.mark.asyncio
async def test_owner_work_predrain_reduces_remaining_with_no_extra_leg(session):
    """Pre-existing owner WORK is consumed by the freeze directly: it lowers
    `remaining` so fewer terminal funds are swept, and produces NO extra ledger
    leg of its own."""
    # Owner already holds 40 WORK. One terminal work 100 fee 0. Request 100.
    owner, (t1,) = await _mk_owner_with_terminals(
        session,
        terminals=[(Decimal("100"), Decimal("0"))],
        owner_work=Decimal("40"),
    )
    svc = FinanceService(session)
    req = await svc.create_merchant_sweep_withdrawal(_create("100"), owner)

    # Pre-drain: take_from_owner = min(40,100)=40 → remaining 60. Terminal supplies
    # 60 (fee 0). Owner WORK = 40 + 60 - 100 = 0. ESCROW = 100.
    assert req.fee_amount == Decimal("0")
    assert await _bal(session, merchant_id=t1.id) == Decimal("40")  # 100 - 60
    assert await _bal(session, user_id=owner.id, btype=BalanceType.WORK) == Decimal("0")
    assert await _bal(session, user_id=owner.id, btype=BalanceType.ESCROW) == Decimal("100")

    # Exactly ONE INTERNAL_TRANSFER (the single terminal) — pre-drain added none.
    it = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.INTERNAL_TRANSFER)
    assert len(it) == 1 and it[0].amount == Decimal("60")
    wd = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.WITHDRAWAL)
    assert len(wd) == 1 and wd[0].amount == Decimal("100")


@pytest.mark.asyncio
async def test_owner_work_fully_covers_request_no_terminal_legs(session):
    """When owner WORK alone covers the request, remaining hits 0 before the
    terminal loop runs → zero INTERNAL_TRANSFER legs, terminal untouched, only
    the WITHDRAWAL freeze of the owner's own WORK."""
    owner, (t1,) = await _mk_owner_with_terminals(
        session,
        terminals=[(Decimal("500"), Decimal("5"))],
        owner_work=Decimal("100"),
    )
    svc = FinanceService(session)
    req = await svc.create_merchant_sweep_withdrawal(_create("100"), owner)

    # No terminal participated → fee 0, terminal WORK untouched.
    assert req.fee_amount == Decimal("0")
    assert await _bal(session, merchant_id=t1.id) == Decimal("500")
    assert await _legs(session, reference_id=req.id,
                       reference_type=LedgerReferenceType.INTERNAL_TRANSFER) == []
    # Owner WORK 100→0 frozen to ESCROW 100.
    assert await _bal(session, user_id=owner.id, btype=BalanceType.WORK) == Decimal("0")
    assert await _bal(session, user_id=owner.id, btype=BalanceType.ESCROW) == Decimal("100")
    wd = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.WITHDRAWAL)
    assert len(wd) == 1 and wd[0].amount == Decimal("100")


@pytest.mark.asyncio
async def test_single_terminal_sweep_behaves_like_simple_withdrawal(session):
    """One terminal, no owner WORK: a single INTERNAL_TRANSFER pulls take+fee
    onto the owner, then the freeze locks amount+fee into owner ESCROW — the
    same end-state shape as an ordinary withdrawal request, just owner-scoped."""
    owner, (t1,) = await _mk_owner_with_terminals(
        session, terminals=[(Decimal("500"), Decimal("3"))],
    )
    svc = FinanceService(session)
    req = await svc.create_merchant_sweep_withdrawal(_create("100"), owner)

    assert req.fee_amount == Decimal("3")
    # Terminal: 500 - (100 + 3) = 397.
    assert await _bal(session, merchant_id=t1.id) == Decimal("397")
    # Owner WORK net 0; ESCROW = 103.
    assert await _bal(session, user_id=owner.id, btype=BalanceType.WORK) == Decimal("0")
    assert await _bal(session, user_id=owner.id, btype=BalanceType.ESCROW) == Decimal("103")
    it = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.INTERNAL_TRANSFER)
    assert len(it) == 1 and it[0].amount == Decimal("103")
    wd = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.WITHDRAWAL)
    assert len(wd) == 1 and wd[0].amount == Decimal("103")


@pytest.mark.asyncio
async def test_sweep_then_approve_debits_owner_escrow_and_pays_system_fee(session):
    """End-to-end: sweep two terminals, then admin approve. Approve takes the
    OWNER-branch (merchant_id is None): amount leaves the platform from owner
    ESCROW, fee lands on the system WORK balance. Value conserved."""
    owner, (t1, t2) = await _mk_owner_with_terminals(
        session,
        terminals=[(Decimal("100"), Decimal("2")), (Decimal("100"), Decimal("3"))],
    )
    svc = FinanceService(session)
    # Request 150: T1 take 98 (rem 52), T2 take 52 (rem 0). Both participate.
    req = await svc.create_merchant_sweep_withdrawal(_create("150"), owner)
    assert req.fee_amount == Decimal("5")
    assert await _bal(session, user_id=owner.id, btype=BalanceType.ESCROW) == Decimal("155")

    out = await svc.approve_withdrawal_request(req.id, ADMIN_ID)
    assert out.status == WithdrawalStatus.SUCCESS

    # ESCROW emptied: 150 out + 5 to system. System WORK = 5. Owner WORK stays 0.
    assert await _bal(session, user_id=owner.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("5")
    assert await _bal(session, user_id=owner.id, btype=BalanceType.WORK) == Decimal("0")

    # Approve adds 1 WITHDRAWAL debit (now 2 total: freeze + debit) and 1 SYSTEM_COMMISSION.
    wd = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.WITHDRAWAL)
    assert len(wd) == 2
    comm = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.SYSTEM_COMMISSION)
    assert len(comm) == 1 and comm[0].amount == Decimal("5")
    # Conservation: frozen 155 = 150 (out) + 5 (system).
    assert Decimal("150") + Decimal("5") == Decimal("155")


@pytest.mark.asyncio
async def test_sweep_then_reject_returns_amount_plus_fee_to_owner_work(session):
    """Reject takes the OWNER-branch: amount+fee returned owner ESCROW→WORK.
    The swept funds now sit on the owner's user-level WORK (not back on the
    terminals — reject does not un-sweep)."""
    owner, (t1,) = await _mk_owner_with_terminals(
        session, terminals=[(Decimal("500"), Decimal("3"))],
    )
    svc = FinanceService(session)
    req = await svc.create_merchant_sweep_withdrawal(_create("100"), owner)
    assert await _bal(session, user_id=owner.id, btype=BalanceType.ESCROW) == Decimal("103")

    out = await svc.reject_withdrawal_request(req.id, ADMIN_ID, reason="bad addr")
    assert out.status == WithdrawalStatus.REJECTED

    # ESCROW→WORK return of 103. Owner WORK 0→103, ESCROW back to 0.
    assert await _bal(session, user_id=owner.id, btype=BalanceType.WORK) == Decimal("103")
    assert await _bal(session, user_id=owner.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")
    # Terminal stays drained (reject does NOT push funds back to the terminal).
    assert await _bal(session, merchant_id=t1.id) == Decimal("397")

    # WITHDRAWAL legs: freeze + return = 2; no system commission.
    wd = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.WITHDRAWAL)
    assert len(wd) == 2
    assert await _legs(session, reference_id=req.id,
                       reference_type=LedgerReferenceType.SYSTEM_COMMISSION) == []


@pytest.mark.asyncio
async def test_exact_boundary_sweep_remaining_hits_zero(session):
    """Request equals the combined spendable EXACTLY (remaining lands on 0):
    every terminal drains to exactly its fee, sweep succeeds, no insufficient
    error at the boundary."""
    # T1 work 100 fee 1 → spendable 99 ; T2 work 60 fee 1 → spendable 59.
    # Combined spendable 158. Request 158.
    owner, (t1, t2) = await _mk_owner_with_terminals(
        session,
        terminals=[(Decimal("100"), Decimal("1")), (Decimal("60"), Decimal("1"))],
    )
    svc = FinanceService(session)
    req = await svc.create_merchant_sweep_withdrawal(_create("158"), owner)

    assert req.fee_amount == Decimal("2")  # both participate
    # Each terminal transfers take+fee = (spendable)+(fee) = its FULL WORK → 0.
    # (The fee is swept along as a reserve, not left behind on the terminal.)
    assert await _bal(session, merchant_id=t1.id) == Decimal("0")  # 100-(99+1)
    assert await _bal(session, merchant_id=t2.id) == Decimal("0")  # 60-(59+1)
    assert await _bal(session, user_id=owner.id, btype=BalanceType.ESCROW) == Decimal("160")  # 158+2
    assert await _bal(session, user_id=owner.id, btype=BalanceType.WORK) == Decimal("0")


@pytest.mark.asyncio
async def test_sweep_then_double_approve_pays_once(session):
    """Idempotency: approving a swept request twice pays out once. The 2nd
    approve re-reads a non-PENDING status (lock is a SQLite no-op) → ConflictException,
    no extra legs, books unchanged."""
    owner, (t1,) = await _mk_owner_with_terminals(
        session, terminals=[(Decimal("500"), Decimal("3"))],
    )
    svc = FinanceService(session)
    req = await svc.create_merchant_sweep_withdrawal(_create("100"), owner)

    await svc.approve_withdrawal_request(req.id, ADMIN_ID)
    escrow_after = await _bal(session, user_id=owner.id, btype=BalanceType.ESCROW)
    system_after = await _bal(session, is_system=True)
    legs_after = len(await _legs(session, reference_id=req.id))

    with pytest.raises(ConflictException):
        await svc.approve_withdrawal_request(req.id, ADMIN_ID)

    assert await _bal(session, user_id=owner.id, btype=BalanceType.ESCROW) == escrow_after == Decimal("0")
    assert await _bal(session, is_system=True) == system_after == Decimal("3")
    assert len(await _legs(session, reference_id=req.id)) == legs_after
    # Total WITHDRAWAL legs stay at 2 (freeze + single debit), never 3.
    assert len(await _legs(session, reference_id=req.id,
                           reference_type=LedgerReferenceType.WITHDRAWAL)) == 2
    assert len(await _legs(session, reference_id=req.id,
                           reference_type=LedgerReferenceType.SYSTEM_COMMISSION)) == 1


# ══════════════════════════════════════════════════════════════════════
# ADVERSARIAL — try to break it
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_insufficient_aggregate_writes_nothing(session):
    """Request exceeds combined spendable across all terminals → ValidationException
    raised BEFORE the begin_nested block, so NO withdrawal row and NO ledger
    legs are written, and balances are completely untouched."""
    # T1 work 50 fee 5 → spendable 45 ; T2 work 30 fee 5 → spendable 25.
    # Combined spendable 70. Request 100 > 70.
    owner, (t1, t2) = await _mk_owner_with_terminals(
        session,
        terminals=[(Decimal("50"), Decimal("5")), (Decimal("30"), Decimal("5"))],
    )
    svc = FinanceService(session)

    wd_before = await _count_withdrawals(session)
    legs_before = await _count_all_legs(session)

    with pytest.raises(ValidationException, match="Insufficient funds"):
        await svc.create_merchant_sweep_withdrawal(_create("100"), owner)

    # No row, no legs, terminals untouched.
    assert await _count_withdrawals(session) == wd_before
    assert await _count_all_legs(session) == legs_before
    assert await _bal(session, merchant_id=t1.id) == Decimal("50")
    assert await _bal(session, merchant_id=t2.id) == Decimal("30")
    assert await _bal(session, user_id=owner.id, btype=BalanceType.ESCROW) == Decimal("0")


@pytest.mark.asyncio
async def test_fee_dominated_terminal_is_skipped_entirely(session):
    """A terminal whose WORK <= its fee (spendable <= 0) is skipped: no leg, its
    fee is NOT counted, its balance is untouched. Only the healthy terminal funds
    the sweep."""
    # T1 work 5 fee 10 → spendable -5 → SKIPPED. T2 work 500 fee 2 → spendable 498.
    owner, (t1, t2) = await _mk_owner_with_terminals(
        session,
        terminals=[(Decimal("5"), Decimal("10")), (Decimal("500"), Decimal("2"))],
    )
    svc = FinanceService(session)
    req = await svc.create_merchant_sweep_withdrawal(_create("100"), owner)

    # Only T2 participates → fee = 2 (T1's fee 10 NOT added).
    assert req.fee_amount == Decimal("2")
    assert await _bal(session, merchant_id=t1.id) == Decimal("5")          # untouched
    assert await _bal(session, merchant_id=t2.id) == Decimal("398")        # 500-(100+2)
    # Exactly ONE INTERNAL_TRANSFER (T2 only).
    it = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.INTERNAL_TRANSFER)
    assert len(it) == 1 and it[0].amount == Decimal("102")
    assert await _bal(session, user_id=owner.id, btype=BalanceType.ESCROW) == Decimal("102")


@pytest.mark.asyncio
async def test_fee_exactly_equals_work_is_skipped(session):
    """Boundary on the skip rule: spendable == 0 (WORK == fee) is `<= 0` → the
    terminal is skipped, not drained to zero."""
    # T1 work 10 fee 10 → spendable 0 → SKIPPED. T2 funds it.
    owner, (t1, t2) = await _mk_owner_with_terminals(
        session,
        terminals=[(Decimal("10"), Decimal("10")), (Decimal("200"), Decimal("1"))],
    )
    svc = FinanceService(session)
    req = await svc.create_merchant_sweep_withdrawal(_create("50"), owner)

    assert req.fee_amount == Decimal("1")  # only T2
    assert await _bal(session, merchant_id=t1.id) == Decimal("10")  # untouched, NOT 0
    it = await _legs(session, reference_id=req.id, reference_type=LedgerReferenceType.INTERNAL_TRANSFER)
    assert len(it) == 1


@pytest.mark.asyncio
async def test_non_merchant_owner_rejected_before_any_db_work(session):
    """A non-MERCHANT user (e.g. TRADER) is rejected with ValidationException
    BEFORE any merchant lookup / balance / ledger work."""
    trader = await _mk_user(session, role=UserRole.TRADER)
    svc = FinanceService(session)

    wd_before = await _count_withdrawals(session)
    legs_before = await _count_all_legs(session)

    with pytest.raises(ValidationException, match="only available for merchant owners"):
        await svc.create_merchant_sweep_withdrawal(_create("100"), trader)

    assert await _count_withdrawals(session) == wd_before
    assert await _count_all_legs(session) == legs_before


@pytest.mark.asyncio
async def test_zero_terminals_rejected(session):
    """A merchant owner with NO terminals → ValidationException, nothing written."""
    owner = await _mk_user(session, role=UserRole.MERCHANT)
    svc = FinanceService(session)

    wd_before = await _count_withdrawals(session)
    legs_before = await _count_all_legs(session)

    with pytest.raises(ValidationException, match="No terminals available"):
        await svc.create_merchant_sweep_withdrawal(_create("100"), owner)

    assert await _count_withdrawals(session) == wd_before
    assert await _count_all_legs(session) == legs_before
