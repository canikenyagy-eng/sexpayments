"""
Integration tests for ДОЛИВ ownership / cancel / refund GUARDS against a REAL
in-memory ledger — the cases beyond ``test_doliv_money_and_limit`` /
``test_doliv_service``:

  * execute by the WRONG executor (claimed by A, executed by B) → DolivForbidden,
    no money moves;
  * cancel by a non-owner → DolivForbidden, no refund;
  * cancel a CLAIMED долив → DolivConflict, no refund;
  * DOUBLE-cancel an unclaimed долив → DolivConflict on the 2nd, NO second
    ESCROW→WORK refund leg (a 2nd refund would MINT USDT);
  * DOUBLE-claim → DolivConflict on the 2nd, trader_id NOT reassigned to the
    second claimer (the claim is exclusive / first-wins);
  * ``FinanceService.refund_doliv`` early-return guard (total ≤ 0 / requester
    None) writes NO ledger leg.

Plus an APP-BUG PIN (current behaviour, NOT a fix): ``DolivService.create``
checks ONLY ``requisite.trader_id == requester.id`` — it NEVER inspects
``is_archived`` / ``is_active`` / ``status`` — so a долив on an ARCHIVED /
inactive / disabled requisite is currently ACCEPTED. The test PINS that
behaviour and is reported in ``app_bugs_found``.

Money model (USDT, at the active platform rate):
  freeze  : requester WORK → ESCROW for amount_usdt + price_usdt
  refund  : requester ESCROW → WORK for amount_usdt + price_usdt

These guard tests assert EXACT Decimal balances, EXACT ledger-leg counts under
``reference_id = "doliv:<payout.id>"`` (a duplicated refund/settle shows up as
an extra leg), and value conservation. SQLite has no real row locks, so the
exclusivity cases are exercised SEQUENTIALLY — the second call runs the same
application-level status re-check the row lock protects under real concurrency.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.cascading import RequisiteSource
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.common.enums.rates import OrderBookSide
from app.common.enums.requisites import RequisiteStatus
from app.modules.doliv.exceptions import DolivConflict, DolivForbidden
from app.modules.doliv.service import DolivService
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.finance.service import FinanceService
from app.modules.payouts.models import Payout
from app.modules.rates.models import RateConfig
from app.modules.requisites.models import Requisite, RequisiteLimit
from app.modules.settings.service import SettingsService
from app.modules.users.models import User

_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF"  # minimal valid PDF for execute receipts


# ── stub the payout/celery worker wiring (долив expiry sweeps live there) ──


@pytest.fixture(autouse=True)
def _stub_celery():
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


# ── builders (self-contained, reused from the долив money-flow template) ───


async def _mk_user(session, *, username, is_blocked=False) -> User:
    from app.common.enums.users import UserRole

    u = User(username=username, password="x", role=UserRole.TRADER, is_system=False, is_blocked=is_blocked)
    session.add(u)
    await session.flush()
    return u


async def _mk_balance(session, amount, *, user_id, btype=BalanceType.WORK) -> Balance:
    b = Balance(user_id=user_id, type=btype, currency=Currency.USDT, amount=Decimal(amount))
    session.add(b)
    await session.flush()
    return b


async def _mk_requisite(
    session, trader_id, *, limit_daily, daily_turnover,
    is_active=True, is_archived=False, status=RequisiteStatus.ENABLED,
) -> Requisite:
    req = Requisite(
        trader_id=trader_id, nickname="r", bank_name="Sber", account_number="40817000",
        account_holder="Ivan", payment_method=PaymentMethod.SBP,
        status=status, currency=Currency.RUB, is_active=is_active,
        is_archived=is_archived, source=RequisiteSource.LOCAL,
    )
    session.add(req)
    await session.flush()
    session.add(RequisiteLimit(
        requisite_id=req.id, limit_daily=Decimal(limit_daily), limit_monthly=Decimal("100000000"),
        current_daily_turnover=Decimal(daily_turnover),
        current_monthly_turnover=Decimal(daily_turnover),
    ))
    await session.flush()
    return req


async def _mk_rate(session, *, currency=Currency.RUB, rate="100") -> None:
    session.add(RateConfig(
        name="r", side=OrderBookSide.SELL, fiat_currency=currency,
        is_active=True, current_rate=float(rate),
    ))
    await session.flush()


async def _configure(session, *, executor_ids, price="10", reward="5", min_a="0", max_a="0") -> None:
    s = SettingsService(session)
    await s.set("doliv_executor_user_ids", executor_ids)
    await s.set("doliv_price_percent", price)
    await s.set("doliv_executor_reward_percent", reward)
    await s.set("doliv_min_amount", min_a)
    await s.set("doliv_max_amount", max_a)


# ── reader helpers ─────────────────────────────────────────────────────────


async def _bal(session, *, user_id=None, is_system=False, btype=BalanceType.WORK) -> Decimal:
    stmt = select(Balance.amount).where(Balance.type == btype, Balance.currency == Currency.USDT)
    if is_system:
        stmt = stmt.where(Balance.user_id.is_(None), Balance.is_system.is_(True))
    else:
        stmt = stmt.where(Balance.user_id == user_id)
    v = (await session.execute(stmt)).scalar_one_or_none()
    return Decimal(str(v)) if v is not None else Decimal("0")


async def _ledger_legs(session, payout_id) -> list:
    """All ledger entries belonging to a долив (``reference_id = "doliv:<id>"``)."""
    ref = f"doliv:{payout_id}"
    rows = (await session.execute(
        select(LedgerEntry).where(LedgerEntry.reference_id == ref).order_by(LedgerEntry.id)
    )).scalars().all()
    return list(rows)


async def _turnover(session, requisite_id) -> tuple:
    lim = (await session.execute(
        select(RequisiteLimit).where(RequisiteLimit.requisite_id == requisite_id)
    )).scalar_one()
    await session.refresh(lim)
    return (Decimal(str(lim.current_daily_turnover)), Decimal(str(lim.current_monthly_turnover)))
# ════════════════════════════════════════════════════════════════════════
# ADVERSARIAL — execute by the WRONG executor
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_execute_by_other_executor_forbidden_no_money(session):
    """Executor B trying to execute a долив CLAIMED by executor A → DolivForbidden
    (``locked.trader_id != executor.id``). No settle: requester stays frozen,
    neither executor is credited, the долив stays CLAIMED, only the freeze leg
    exists. A then executes normally → exactly the 3 settle legs appear."""
    requester = await _mk_user(session, username="req")
    exec_a = await _mk_user(session, username="exe_a")
    exec_b = await _mk_user(session, username="exe_b")
    await _mk_balance(session, "1000", user_id=requester.id)
    await _mk_rate(session, rate="100")
    # BOTH are authorised доливщики — so the guard under test is the per-долив
    # claimer check, NOT the executor-ACL check.
    await _configure(session, executor_ids=f"{exec_a.id},{exec_b.id}", price="10", reward="5")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))
    await svc.claim(exec_a, str(doliv.uuid))  # A owns it

    escrow_before = await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW)
    with pytest.raises(DolivForbidden):
        await svc.execute(exec_b, str(doliv.uuid), receipt_content=_PDF, receipt_filename="receipt.pdf")

    # No money moved; долив still CLAIMED to A; nothing credited to either exec.
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == escrow_before
    assert await _bal(session, user_id=exec_a.id) == Decimal("0.0000")
    assert await _bal(session, user_id=exec_b.id) == Decimal("0.0000")
    assert len(await _ledger_legs(session, doliv.id)) == 1  # freeze only
    assert (await svc._get_doliv(str(doliv.uuid))).status == PayoutStatus.CLAIMED

    # The rightful claimer A can still settle it → exactly 3 settle legs land.
    await svc.execute(exec_a, str(doliv.uuid), receipt_content=_PDF, receipt_filename="receipt.pdf")
    assert await _bal(session, user_id=exec_a.id) == Decimal("1.0500")   # amount + reward
    assert await _bal(session, user_id=exec_b.id) == Decimal("0.0000")
    assert len(await _ledger_legs(session, doliv.id)) == 4               # freeze + 3 settle
    assert await _turnover(session, req.id) == (Decimal("5000.00"), Decimal("5000.00"))

@pytest.mark.asyncio
async def test_double_claim_conflict_trader_not_reassigned(session):
    """SEQUENTIAL exclusivity: once executor A has CLAIMED a долив, executor B's
    claim is rejected (status != CREATED → DolivConflict) and the долив's
    ``trader_id`` is NOT reassigned to B — the claim is first-wins. No money is
    involved in a claim, so only the freeze leg exists throughout."""
    requester = await _mk_user(session, username="req")
    exec_a = await _mk_user(session, username="exe_a")
    exec_b = await _mk_user(session, username="exe_b")
    await _mk_balance(session, "1000", user_id=requester.id)
    await _mk_rate(session, rate="100")
    await _configure(session, executor_ids=f"{exec_a.id},{exec_b.id}", price="10", reward="5")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))
    claimed = await svc.claim(exec_a, str(doliv.uuid))
    assert claimed.status == PayoutStatus.CLAIMED
    assert claimed.trader_id == exec_a.id

    with pytest.raises(DolivConflict):
        await svc.claim(exec_b, str(doliv.uuid))

    # Owner of the claim is still A; claim never touches money.
    after = await svc._get_doliv(str(doliv.uuid))
    assert after.status == PayoutStatus.CLAIMED
    assert after.trader_id == exec_a.id                              # NOT reassigned to B
    assert len(await _ledger_legs(session, doliv.id)) == 1           # freeze only
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == Decimal("1.1000")


# ════════════════════════════════════════════════════════════════════════
# ADVERSARIAL — refund_doliv early-return writes no leg
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_refund_doliv_early_returns_write_no_leg(session):
    """``FinanceService.refund_doliv`` short-circuits (``total <= 0 or requester
    is None``) and writes NOTHING. Directly exercise both branches against a
    persisted долив-shaped Payout:
      (a) requester is None         → no leg;
      (b) total <= 0 (amount_usdt == price_usdt == 0) → no leg.
    Either branch leaving a ledger entry would be a phantom ESCROW→WORK credit."""
    requester = await _mk_user(session, username="req")
    await _mk_balance(session, "1000", user_id=requester.id)
    await _mk_balance(session, "0", user_id=requester.id, btype=BalanceType.ESCROW)
    fin = FinanceService(session)

    # A persisted долив-shaped payout so reference_id "doliv:<id>" is well-formed.
    from uuid import uuid4

    payout = Payout(
        uuid=uuid4(), external_id=f"doliv-{uuid4().hex}", is_doliv=True,
        requester_trader_id=requester.id, refill_requisite_id=None, trader_id=None,
        payment_method=PaymentMethod.SBP, amount=Decimal("0"), currency=Currency.RUB,
        amount_usdt=Decimal("0"), exchange_rate=Decimal("100"),
        doliv_price_usdt=Decimal("0"), trader_fee_usdt=Decimal("0"),
        req_holder="Ivan", req_number="40817000", req_extra="Sber",
        status=PayoutStatus.CREATED,
    )
    session.add(payout)
    await session.flush()

    # (a) requester None → early-return, no leg.
    await fin.refund_doliv(payout, None)
    assert await _ledger_legs(session, payout.id) == []

    # (b) total == 0 (amount + price = 0) → early-return, no leg, even WITH a requester.
    await fin.refund_doliv(payout, requester)
    assert await _ledger_legs(session, payout.id) == []

    # Balances untouched by both no-op refunds.
    assert await _bal(session, user_id=requester.id) == Decimal("1000.0000")
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == Decimal("0.0000")


# ════════════════════════════════════════════════════════════════════════
# APP-BUG PIN (current behaviour — NOT fixed): create on an archived /
# inactive / disabled requisite is ACCEPTED.
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_BUG_create_accepts_archived_inactive_disabled_requisite(session):
    """APP-BUG (pinned, NOT fixed): ``DolivService.create`` validates ONLY
    ``requisite.trader_id == requester.id`` — it NEVER checks ``is_archived`` /
    ``is_active`` / ``status``. So a долив against an ARCHIVED + inactive +
    DISABLED requisite (one that can take no real traffic) is currently ACCEPTED:
    the requester's funds are frozen and the долив enters the pool.

    This test PINS the CURRENT (buggy) behaviour — if create later starts
    rejecting dead requisites this test will fail and must be updated. Reported
    in app_bugs_found. Compare with the payin pooling path, which DOES push
    is_active / is_archived / status exclusions into SQL."""
    requester = await _mk_user(session, username="req")
    await _mk_balance(session, "1000", user_id=requester.id)
    await _mk_rate(session, rate="100")
    await _configure(session, executor_ids="0", price="10", reward="5")
    # A requisite that is archived AND inactive AND DISABLED — fully dead.
    req = await _mk_requisite(
        session, requester.id, limit_daily="5000", daily_turnover="0",
        is_active=False, is_archived=True, status=RequisiteStatus.DISABLED,
    )

    svc = DolivService(session)
    # BUG: accepted despite the requisite being dead — no ValidationException.
    doliv = await svc.create(requester, req.id, Decimal("100"))
    assert doliv.status == PayoutStatus.CREATED
    assert doliv.refill_requisite_id == req.id
    # Funds WERE frozen against the dead requisite (1.0 + 0.10 price).
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == Decimal("1.1000")
    assert await _bal(session, user_id=requester.id) == Decimal("998.9000")
    assert len(await svc.list_mine(requester)) == 1
