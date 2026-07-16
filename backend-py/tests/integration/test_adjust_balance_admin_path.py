"""
Integration tests for ``FinanceService.adjust_balance`` against a REAL
in-memory ledger — the actual admin credit/debit path behind
``POST /admin/adjust`` (``app/api/v1/endpoints/finances.py::admin_adjust_balance``
calls ``service.adjust_balance``; ``deposit()`` has no production caller).

Model (``app/modules/finance/service.py::adjust_balance``):
  * positive delta → DEPOSIT credit-only leg (``to_balance_id`` set, no ``from``):
                     balance grows by delta.
  * negative delta → WITHDRAWAL debit-only leg (``from_balance_id`` set, no ``to``),
                     amount = ``-delta``: balance shrinks. Funds-checked by
                     ``transfer`` — cannot drive a balance negative.
  * zero delta     → returns ``None``, writes NO ledger row.
  * ``reference_id``: supplied value wins; otherwise defaults to ``balance:{id}``.

No row-locking is needed for any assertion here: we check exact Decimal balances,
the one-sided ledger leg, value conservation, and SEQUENTIAL idempotency
(calling the path twice in a row).
"""
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.users import UserRole
from app.core.exceptions import ValidationException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.finance.service import FinanceService
from app.modules.users.models import User


# ── builders (self-contained; cf. test_payout_money_flow.py) ────────────


async def _mk_user(session, *, username, role=UserRole.TRADER) -> User:
    u = User(
        username=username, password=get_password_hash("pass12345"), role=role,
        totp_enabled=False, is_blocked=False, use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_balance(session, amount, *, user_id, btype=BalanceType.WORK,
                      currency=Currency.USDT) -> Balance:
    b = Balance(user_id=user_id, is_system=False, type=btype,
                currency=currency, amount=Decimal(amount))
    session.add(b)
    await session.flush()
    return b


# ── readers / ledger helpers ────────────────────────────────────────────


async def _bal(session, balance_id) -> Decimal:
    row = await session.get(Balance, balance_id)
    await session.refresh(row)
    return Decimal(str(row.amount))


async def _legs_for_ref(session, ref_id) -> list:
    """Every ledger row carrying ``reference_id == ref_id``, oldest first.
    A second (non-deduped) adjustment shows up as extra legs."""
    rows = (await session.execute(
        select(LedgerEntry)
        .where(LedgerEntry.reference_id == ref_id)
        .order_by(LedgerEntry.id)
    )).scalars().all()
    return list(rows)


async def _legs_for_balance(session, balance_id) -> list:
    """Every ledger row that touches ``balance_id`` on either side."""
    rows = (await session.execute(
        select(LedgerEntry)
        .where(
            (LedgerEntry.from_balance_id == balance_id)
            | (LedgerEntry.to_balance_id == balance_id)
        )
        .order_by(LedgerEntry.id)
    )).scalars().all()
    return list(rows)


async def _sum_in(session, balance_id) -> Decimal:
    v = (await session.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .where(LedgerEntry.to_balance_id == balance_id)
    )).scalar_one()
    return Decimal(str(v))


async def _sum_out(session, balance_id) -> Decimal:
    v = (await session.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0))
        .where(LedgerEntry.from_balance_id == balance_id)
    )).scalar_one()
    return Decimal(str(v))


async def _setup(session, *, start=Decimal("0"), btype=BalanceType.WORK):
    user = await _mk_user(session, username=f"u_{id(object())}")
    balance = await _mk_balance(session, start, user_id=user.id, btype=btype)
    return user, balance


# ── CORRECT: positive delta → DEPOSIT credit-only leg ───────────────────


@pytest.mark.asyncio
async def test_positive_delta_credits_balance_one_deposit_leg(session):
    _u, balance = await _setup(session, start=Decimal("100"))
    fin = FinanceService(session)

    entry = await fin.adjust_balance(balance, Decimal("25.5000"), reason="admin top-up")

    assert entry is not None
    assert await _bal(session, balance.id) == Decimal("125.5000")

    legs = await _legs_for_balance(session, balance.id)
    assert len(legs) == 1
    leg = legs[0]
    # Credit-only: money flows INTO the balance, nothing leaves.
    assert leg.reference_type == LedgerReferenceType.DEPOSIT
    assert leg.to_balance_id == balance.id
    assert leg.from_balance_id is None
    assert Decimal(str(leg.amount)) == Decimal("25.5000")
    assert leg.description == "admin top-up"
    # Value conservation: net change == sum(in) - sum(out).
    assert await _sum_in(session, balance.id) - await _sum_out(session, balance.id) \
        == Decimal("25.5000")


# ── CORRECT: negative delta → WITHDRAWAL debit-only leg ─────────────────


@pytest.mark.asyncio
async def test_negative_delta_debits_balance_one_withdrawal_leg(session):
    _u, balance = await _setup(session, start=Decimal("100"))
    fin = FinanceService(session)

    entry = await fin.adjust_balance(balance, Decimal("-30.0000"), reason="admin clawback")

    assert entry is not None
    assert await _bal(session, balance.id) == Decimal("70.0000")

    legs = await _legs_for_balance(session, balance.id)
    assert len(legs) == 1
    leg = legs[0]
    # Debit-only: money flows OUT of the balance, nowhere to go.
    assert leg.reference_type == LedgerReferenceType.WITHDRAWAL
    assert leg.from_balance_id == balance.id
    assert leg.to_balance_id is None
    # The leg stores the POSITIVE magnitude (amount = -delta).
    assert Decimal(str(leg.amount)) == Decimal("30.0000")
    assert leg.description == "admin clawback"
    assert await _sum_in(session, balance.id) - await _sum_out(session, balance.id) \
        == Decimal("-30.0000")


@pytest.mark.asyncio
async def test_negative_delta_to_exact_zero_allowed(session):
    """Debiting the FULL balance is allowed (transfer rejects only amount > balance)."""
    _u, balance = await _setup(session, start=Decimal("40.0000"))
    fin = FinanceService(session)

    await fin.adjust_balance(balance, Decimal("-40.0000"), reason="drain")

    assert await _bal(session, balance.id) == Decimal("0.0000")
    legs = await _legs_for_balance(session, balance.id)
    assert len(legs) == 1
    assert legs[0].reference_type == LedgerReferenceType.WITHDRAWAL


# ── CORRECT: zero delta → None, no ledger row ───────────────────────────


@pytest.mark.asyncio
async def test_zero_delta_is_noop_returns_none_no_ledger_row(session):
    _u, balance = await _setup(session, start=Decimal("100"))
    fin = FinanceService(session)

    entry = await fin.adjust_balance(balance, Decimal("0"), reason="noop")

    assert entry is None
    assert await _bal(session, balance.id) == Decimal("100")
    assert await _legs_for_balance(session, balance.id) == []


# ── CORRECT: supplied reference_id wins over the 'balance:{id}' default ──


@pytest.mark.asyncio
async def test_supplied_reference_id_wins(session):
    _u, balance = await _setup(session, start=Decimal("100"))
    fin = FinanceService(session)

    await fin.adjust_balance(
        balance, Decimal("10"), reason="r", reference_id="admin_adjust_7_99",
    )

    # The supplied ref is used; the default 'balance:{id}' is NOT.
    assert len(await _legs_for_ref(session, "admin_adjust_7_99")) == 1
    assert await _legs_for_ref(session, f"balance:{balance.id}") == []


@pytest.mark.asyncio
async def test_default_reference_id_when_none_supplied(session):
    _u, balance = await _setup(session, start=Decimal("100"))
    fin = FinanceService(session)

    await fin.adjust_balance(balance, Decimal("-5"), reason="r")

    legs = await _legs_for_ref(session, f"balance:{balance.id}")
    assert len(legs) == 1
    assert legs[0].reference_type == LedgerReferenceType.WITHDRAWAL


# ── ADVERSARIAL: cannot drive a balance negative ────────────────────────


@pytest.mark.asyncio
async def test_debit_beyond_balance_raises_and_leaves_balance_untouched(session):
    """delta magnitude > balance → transfer's funds-check raises; the balance
    is UNCHANGED and NO ledger leg is written (no going negative)."""
    _u, balance = await _setup(session, start=Decimal("20.0000"))
    fin = FinanceService(session)

    with pytest.raises(ValidationException, match="Insufficient funds"):
        await fin.adjust_balance(balance, Decimal("-20.0001"), reason="overdraw")

    assert await _bal(session, balance.id) == Decimal("20.0000")
    assert await _legs_for_balance(session, balance.id) == []


# ── ADVERSARIAL / app-behavior-pin: NO idempotency / dedup ──────────────


@pytest.mark.asyncio
async def test_same_positive_delta_twice_same_reference_id_credits_TWICE(session):
    """PIN CURRENT BEHAVIOR (potential bug): adjust_balance does NOT dedup on
    reference_id. Applying the same positive delta twice with the SAME
    reference_id credits the balance TWICE and writes TWO ledger legs.

    Contrast with order-settlement / withdrawal paths which guard against a
    repeated call. The admin /admin/adjust path derives its ref_id from
    ``admin_adjust_{admin_id}_{balance_id}`` (constant across retries), so a
    duplicated request double-credits. Pinned, not 'fixed', per task rules.
    """
    _u, balance = await _setup(session, start=Decimal("100"))
    fin = FinanceService(session)
    ref = "admin_adjust_1_1"

    await fin.adjust_balance(balance, Decimal("10"), reason="dup", reference_id=ref)
    await fin.adjust_balance(balance, Decimal("10"), reason="dup", reference_id=ref)

    # Double-applied: +20, and TWO legs share the ref (no dedup).
    assert await _bal(session, balance.id) == Decimal("120")
    assert len(await _legs_for_ref(session, ref)) == 2


@pytest.mark.asyncio
async def test_same_negative_delta_twice_same_reference_id_debits_TWICE(session):
    """PIN CURRENT BEHAVIOR: a repeated debit with the same reference_id also
    applies twice (until the balance runs out, which is then a hard funds-check
    failure — not idempotency). Two successful debits → balance down 2×."""
    _u, balance = await _setup(session, start=Decimal("100"))
    fin = FinanceService(session)
    ref = "admin_adjust_1_2"

    await fin.adjust_balance(balance, Decimal("-15"), reason="dup", reference_id=ref)
    await fin.adjust_balance(balance, Decimal("-15"), reason="dup", reference_id=ref)

    assert await _bal(session, balance.id) == Decimal("70")
    assert len(await _legs_for_ref(session, ref)) == 2


# ── value conservation across a mixed credit+debit sequence ─────────────


@pytest.mark.asyncio
async def test_mixed_sequence_balance_equals_ledger_net(session):
    """Balance after a credit+debit+credit sequence equals start + ledger net
    (sum_in - sum_out), proving every move went through the ledger."""
    _u, balance = await _setup(session, start=Decimal("50.0000"))
    fin = FinanceService(session)

    await fin.adjust_balance(balance, Decimal("100.0000"), reason="c1")
    await fin.adjust_balance(balance, Decimal("-30.0000"), reason="d1")
    await fin.adjust_balance(balance, Decimal("0"), reason="noop")  # no leg
    await fin.adjust_balance(balance, Decimal("12.3400"), reason="c2")

    expected = Decimal("50.0000") + Decimal("100.0000") - Decimal("30.0000") + Decimal("12.3400")
    assert await _bal(session, balance.id) == expected

    net = await _sum_in(session, balance.id) - await _sum_out(session, balance.id)
    assert net == expected - Decimal("50.0000")
    # Exactly three legs (the zero delta wrote nothing).
    assert len(await _legs_for_balance(session, balance.id)) == 3
