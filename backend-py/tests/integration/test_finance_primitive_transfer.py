"""
Integration tests for the FinanceService.transfer double-entry PRIMITIVE,
against a REAL in-memory ledger. This is the lowest-level money mover that
every higher-level flow (freeze/settle/refund/release/doliv/withdrawal/…)
ultimately calls, so its guards and conservation properties are money-critical.

`transfer(amount, currency, reference_type, reference_id, from_balance_id?,
to_balance_id?)` rules under test:

  * CORRECT single-sided CREDIT  (from=None, to set) → grows the to-balance,
    ledger row has from_balance_id=None (mint from outside, e.g. DEPOSIT).
  * CORRECT single-sided DEBIT   (from set, to=None) → shrinks the from-balance
    with a funds-check, ledger row has to_balance_id=None (burn to outside).
  * CORRECT two-sided move        → debits from, credits to, exactly conserves.

  Adversarial:
  * amount == 0 / amount < 0      → ValidationException "must be positive".
  * currency mismatch on the TO   → ValidationException (separate branch from
    the FROM-side mismatch already covered in tests/unit/test_finance_service).
  * from_balance_id == to_balance_id (same id) → documented net effect: deduct
    then add on the SAME row ⇒ net ZERO, no mint, single ledger leg.
  * insufficient funds            → raises AFTER the lock but BEFORE deduction;
    BOTH balances must be UNCHANGED (no partial mutation).
  * bad from_balance_id / bad to_balance_id → "Balance {id} not found".

Notes for SQLite harness: SELECT…FOR UPDATE is a no-op, so we do NOT assert
anything that needs real row-locking. We assert exact Decimal balances, exact
ledger-leg counts, and value conservation (sum debited == sum credited).
"""
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.core.exceptions import ValidationException
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.finance.service import FinanceService

REF = LedgerReferenceType.INTERNAL_TRANSFER


# ── builders / readers ───────────────────────────────────────────────────


async def _mk_balance(session, amount, *, currency=Currency.USDT,
                      btype=BalanceType.WORK, is_system=False) -> Balance:
    """A bare balance row. user_id/merchant_id left NULL on purpose — the
    transfer primitive operates purely on balance ids, owner is irrelevant."""
    b = Balance(is_system=is_system, type=btype, currency=currency,
                amount=Decimal(amount))
    session.add(b)
    await session.flush()
    return b


async def _bal(session, balance_id) -> Decimal:
    """Re-read a balance's amount straight from the DB (fresh, no stale cache)."""
    row = await session.get(Balance, balance_id)
    await session.refresh(row)
    return row.amount


async def _legs(session, *, reference_id, reference_type=REF):
    stmt = select(LedgerEntry).where(
        LedgerEntry.reference_id == reference_id,
        LedgerEntry.reference_type == reference_type,
    )
    return (await session.execute(stmt)).scalars().all()


async def _total_money(session) -> Decimal:
    """Sum of every balance row — used as a conservation invariant across a
    two-sided transfer (internal moves must never change the system total)."""
    total = (await session.execute(select(func.coalesce(func.sum(Balance.amount), 0)))).scalar()
    return Decimal(total)


# ══════════════════════════════════════════════════════════════════════════
# CORRECT behaviour
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_single_sided_credit_mints_into_to_balance(session):
    """from=None, to set → to-balance grows by amount; ledger from_balance_id is
    None (money entering the system from outside, e.g. a DEPOSIT)."""
    to = await _mk_balance(session, "10")
    fin = FinanceService(session)

    entry = await fin.transfer(
        amount=Decimal("40"), currency=Currency.USDT,
        reference_type=REF, reference_id="credit-1",
        from_balance_id=None, to_balance_id=to.id,
    )

    assert await _bal(session, to.id) == Decimal("50")
    assert entry.from_balance_id is None
    assert entry.to_balance_id == to.id
    assert entry.amount == Decimal("40")
    legs = await _legs(session, reference_id="credit-1")
    assert len(legs) == 1


@pytest.mark.asyncio
async def test_single_sided_debit_burns_from_balance(session):
    """from set, to=None → from-balance shrinks by amount (funds-check applies);
    ledger to_balance_id is None (money leaving the system, e.g. a WITHDRAWAL)."""
    frm = await _mk_balance(session, "100")
    fin = FinanceService(session)

    entry = await fin.transfer(
        amount=Decimal("30"), currency=Currency.USDT,
        reference_type=REF, reference_id="debit-1",
        from_balance_id=frm.id, to_balance_id=None,
    )

    assert await _bal(session, frm.id) == Decimal("70")
    assert entry.from_balance_id == frm.id
    assert entry.to_balance_id is None
    assert entry.amount == Decimal("30")
    assert len(await _legs(session, reference_id="debit-1")) == 1


@pytest.mark.asyncio
async def test_two_sided_move_conserves_value_exactly(session):
    """Debit from + credit to in one call; total money in the system unchanged,
    a single ledger leg records both sides."""
    frm = await _mk_balance(session, "100")
    to = await _mk_balance(session, "25")
    fin = FinanceService(session)
    before_total = await _total_money(session)

    await fin.transfer(
        amount=Decimal("60"), currency=Currency.USDT,
        reference_type=REF, reference_id="move-1",
        from_balance_id=frm.id, to_balance_id=to.id,
    )

    assert await _bal(session, frm.id) == Decimal("40")
    assert await _bal(session, to.id) == Decimal("85")
    # sum-in == sum-out ⇒ the system total is invariant.
    assert await _total_money(session) == before_total
    assert len(await _legs(session, reference_id="move-1")) == 1


@pytest.mark.asyncio
async def test_exact_funds_boundary_allows_transfer(session):
    """amount == from_balance.amount is allowed (guard is `<`, not `<=`):
    drains the from-balance to exactly zero."""
    frm = await _mk_balance(session, "75")
    to = await _mk_balance(session, "0")
    fin = FinanceService(session)

    await fin.transfer(
        amount=Decimal("75"), currency=Currency.USDT,
        reference_type=REF, reference_id="boundary-1",
        from_balance_id=frm.id, to_balance_id=to.id,
    )

    assert await _bal(session, frm.id) == Decimal("0")
    assert await _bal(session, to.id) == Decimal("75")


# ══════════════════════════════════════════════════════════════════════════
# ADVERSARIAL — amount guard
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_amount_zero_rejected_no_money_moves(session):
    """amount == 0 → ValidationException "must be positive"; the `<= 0` guard
    fires before any balance is touched or any ledger row is written."""
    frm = await _mk_balance(session, "100")
    to = await _mk_balance(session, "100")
    fin = FinanceService(session)

    with pytest.raises(ValidationException, match="must be positive"):
        await fin.transfer(
            amount=Decimal("0"), currency=Currency.USDT,
            reference_type=REF, reference_id="zero-1",
            from_balance_id=frm.id, to_balance_id=to.id,
        )

    assert await _bal(session, frm.id) == Decimal("100")
    assert await _bal(session, to.id) == Decimal("100")
    assert await _legs(session, reference_id="zero-1") == []


@pytest.mark.asyncio
async def test_amount_negative_rejected_no_money_moves(session):
    """amount < 0 → "must be positive" (a negative amount would otherwise MINT
    on the from side and BURN on the to side — catastrophic). No mutation."""
    frm = await _mk_balance(session, "100")
    to = await _mk_balance(session, "100")
    fin = FinanceService(session)

    with pytest.raises(ValidationException, match="must be positive"):
        await fin.transfer(
            amount=Decimal("-5"), currency=Currency.USDT,
            reference_type=REF, reference_id="neg-1",
            from_balance_id=frm.id, to_balance_id=to.id,
        )

    assert await _bal(session, frm.id) == Decimal("100")
    assert await _bal(session, to.id) == Decimal("100")
    assert await _legs(session, reference_id="neg-1") == []


# ══════════════════════════════════════════════════════════════════════════
# ADVERSARIAL — currency mismatch on the TO side (separate branch)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_currency_mismatch_on_to_side_rejected(session):
    """TO balance currency != transfer currency → ValidationException. Isolated
    to the TO branch by using from=None so the FROM-side check never runs."""
    to = await _mk_balance(session, "10", currency=Currency.AZN)
    fin = FinanceService(session)

    with pytest.raises(ValidationException, match="Currency mismatch on to_balance"):
        await fin.transfer(
            amount=Decimal("5"), currency=Currency.USDT,
            reference_type=REF, reference_id="ccy-to-1",
            from_balance_id=None, to_balance_id=to.id,
        )

    # to-balance untouched, no ledger leg.
    assert await _bal(session, to.id) == Decimal("10")
    assert await _legs(session, reference_id="ccy-to-1") == []


@pytest.mark.asyncio
async def test_currency_mismatch_on_to_side_does_not_credit_after_valid_from(session):
    """Two-sided move where FROM matches but TO mismatches: the FROM side IS
    deducted in-memory BEFORE the TO check raises (deduction happens at line
    ~386, TO currency check at ~390). This PINS that ordering: the call raises,
    no ledger row is written, and because the surrounding caller is expected to
    roll the tx back, we assert the *ledger* never recorded the move. We do NOT
    assert the in-memory from-balance here (that mutation is the caller's job to
    roll back via the transaction, which is the documented contract)."""
    frm = await _mk_balance(session, "100", currency=Currency.USDT)
    to = await _mk_balance(session, "10", currency=Currency.AZN)
    fin = FinanceService(session)

    with pytest.raises(ValidationException, match="Currency mismatch on to_balance"):
        await fin.transfer(
            amount=Decimal("20"), currency=Currency.USDT,
            reference_type=REF, reference_id="ccy-to-2",
            from_balance_id=frm.id, to_balance_id=to.id,
        )

    # The to-balance is never credited and no ledger leg exists.
    assert await _bal(session, to.id) == Decimal("10")
    assert await _legs(session, reference_id="ccy-to-2") == []


# ══════════════════════════════════════════════════════════════════════════
# ADVERSARIAL — same balance on both sides (from == to)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_same_balance_both_sides_nets_to_zero_no_mint(session):
    """from_balance_id == to_balance_id: balance_ids dedupes to a single locked
    row, so the SAME object is both debited and credited → net ZERO change, no
    money is minted. A single ledger leg is written with from==to.

    Documented net effect under test: balance is conserved (deduct `amount`
    then add `amount` back on the same row), NOT doubled, NOT zeroed-out."""
    b = await _mk_balance(session, "100")
    fin = FinanceService(session)
    before_total = await _total_money(session)

    entry = await fin.transfer(
        amount=Decimal("40"), currency=Currency.USDT,
        reference_type=REF, reference_id="self-1",
        from_balance_id=b.id, to_balance_id=b.id,
    )

    # Net effect: unchanged (deducted 40, added 40 back to the same row).
    assert await _bal(session, b.id) == Decimal("100")
    # No mint: system total is invariant.
    assert await _total_money(session) == before_total
    # One ledger leg, both sides point at the same balance.
    assert entry.from_balance_id == b.id
    assert entry.to_balance_id == b.id
    assert len(await _legs(session, reference_id="self-1")) == 1


@pytest.mark.asyncio
async def test_same_balance_both_sides_still_enforces_funds_check(session):
    """Even when from==to (net zero), the funds-check still runs against the
    current balance: amount > balance → "Insufficient funds", nothing written."""
    b = await _mk_balance(session, "30")
    fin = FinanceService(session)

    with pytest.raises(ValidationException, match="Insufficient funds"):
        await fin.transfer(
            amount=Decimal("50"), currency=Currency.USDT,
            reference_type=REF, reference_id="self-2",
            from_balance_id=b.id, to_balance_id=b.id,
        )

    assert await _bal(session, b.id) == Decimal("30")
    assert await _legs(session, reference_id="self-2") == []


# ══════════════════════════════════════════════════════════════════════════
# ADVERSARIAL — insufficient funds: raise AFTER lock, BEFORE deduction
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_insufficient_funds_leaves_both_balances_unchanged(session):
    """amount > from_balance.amount → "Insufficient funds". The check sits
    between locking and the deduction, so NEITHER balance is mutated (no partial
    debit on from, no partial credit on to) and no ledger leg is written."""
    frm = await _mk_balance(session, "100")
    to = await _mk_balance(session, "50")
    fin = FinanceService(session)

    with pytest.raises(ValidationException, match="Insufficient funds"):
        await fin.transfer(
            amount=Decimal("150"), currency=Currency.USDT,
            reference_type=REF, reference_id="insuf-1",
            from_balance_id=frm.id, to_balance_id=to.id,
        )

    # No partial mutation on EITHER side.
    assert await _bal(session, frm.id) == Decimal("100")
    assert await _bal(session, to.id) == Decimal("50")
    assert await _legs(session, reference_id="insuf-1") == []


# ══════════════════════════════════════════════════════════════════════════
# ADVERSARIAL — unknown balance ids
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_unknown_from_balance_id_raises_not_found(session):
    """A from_balance_id that does not exist → "Balance {id} not found"."""
    fin = FinanceService(session)
    missing = 9_999_001

    with pytest.raises(ValidationException, match=f"Balance {missing} not found"):
        await fin.transfer(
            amount=Decimal("10"), currency=Currency.USDT,
            reference_type=REF, reference_id="badfrom-1",
            from_balance_id=missing, to_balance_id=None,
        )

    assert await _legs(session, reference_id="badfrom-1") == []


@pytest.mark.asyncio
async def test_unknown_to_balance_id_raises_not_found(session):
    """A to_balance_id that does not exist → "Balance {id} not found"; the
    real from-balance must NOT be debited (lock loop fails before any mutation)."""
    frm = await _mk_balance(session, "100")
    fin = FinanceService(session)
    missing = 9_999_002

    with pytest.raises(ValidationException, match=f"Balance {missing} not found"):
        await fin.transfer(
            amount=Decimal("10"), currency=Currency.USDT,
            reference_type=REF, reference_id="badto-1",
            from_balance_id=frm.id, to_balance_id=missing,
        )

    # from-balance untouched (the not-found check runs in the lock loop, which
    # happens before the from-side deduction), and no ledger leg written.
    assert await _bal(session, frm.id) == Decimal("100")
    assert await _legs(session, reference_id="badto-1") == []
