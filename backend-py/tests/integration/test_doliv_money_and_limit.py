"""
Integration tests for the ДОЛИВ (requisite refill) money flow against a REAL
in-memory ledger, PLUS the pending-counts-against-the-daily-limit guard (#24/#25).

Долив reuses the ``Payout`` entity (``is_doliv=True``) but its money lives in
``FinanceService.{freeze,settle,refund}_doliv`` and its lifecycle in
``DolivService``. The долив is requisite-anchored and priced at the CURRENT
platform rate for the requisite's currency (no order with a fixed rate).

Money model (USDT, at the active platform rate):
  freeze  : requester WORK → ESCROW for amount_usdt + price_usdt
  settle  : requester ESCROW → доливщик WORK (amount); requester ESCROW → system
            (price); system → доливщик WORK (executor reward); turnover += amount
  refund  : requester ESCROW → WORK for amount_usdt + price_usdt

These tests assert EXACT Decimal balances, money conservation (sum that leaves the
requester == sum that reaches the доливщик + the platform), the ledger-entry legs
(долив legs carry ``reference_id = "doliv:<payout.id>"``), and the pending-cap
guard. SQLite has no real row locks, so the "two доливы can't both pass" case is
verified SEQUENTIALLY — the second create runs the same application-level
remaining-capacity re-check the row lock protects under real concurrency.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import func, select

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.cascading import RequisiteSource
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.common.enums.rates import OrderBookSide
from app.common.enums.requisites import RequisiteStatus
from app.common.enums.users import UserRole
from app.core.exceptions import AppException, ValidationException
from app.modules.doliv.exceptions import DolivConflict
from app.modules.doliv.service import DolivService
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.rates.models import RateConfig
from app.modules.requisites.models import Requisite, RequisiteLimit
from app.modules.settings.service import SettingsService
from app.modules.users.models import User

_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF"  # minimal valid PDF for execute receipts

_Q = Decimal("0.0000")


# ── stub the payout/celery worker wiring (долив expiry sweeps live there) ──


@pytest.fixture(autouse=True)
def _stub_celery():
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


# ── builders (self-contained) ─────────────────────────────────────────────


async def _mk_user(session, *, username, role=UserRole.TRADER) -> User:
    u = User(username=username, password="x", role=role, is_system=False, is_blocked=False)
    session.add(u)
    await session.flush()
    return u


async def _mk_balance(session, amount, *, user_id, btype=BalanceType.WORK) -> Balance:
    b = Balance(user_id=user_id, type=btype, currency=Currency.USDT, amount=Decimal(amount))
    session.add(b)
    await session.flush()
    return b


async def _mk_requisite(session, trader_id, *, limit_daily, daily_turnover) -> Requisite:
    req = Requisite(
        trader_id=trader_id, nickname="r", bank_name="Sber", account_number="40817000",
        account_holder="Ivan", payment_method=PaymentMethod.SBP,
        status=RequisiteStatus.ENABLED, currency=Currency.RUB, is_active=True,
        is_archived=False, source=RequisiteSource.LOCAL,
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


async def _turnover(session, requisite_id) -> tuple:
    lim = (await session.execute(
        select(RequisiteLimit).where(RequisiteLimit.requisite_id == requisite_id)
    )).scalar_one()
    await session.refresh(lim)
    return (Decimal(str(lim.current_daily_turnover)), Decimal(str(lim.current_monthly_turnover)))


async def _ledger_legs(session, payout_id) -> list:
    """All ledger entries belonging to a долив. Долив legs carry
    ``reference_id = "doliv:<payout.id>"`` across DOLIV / SYSTEM_COMMISSION /
    TRADER_REWARD reference_types — a double-settle shows up as extra legs."""
    ref = f"doliv:{payout_id}"
    rows = (await session.execute(
        select(LedgerEntry).where(LedgerEntry.reference_id == ref).order_by(LedgerEntry.id)
    )).scalars().all()
    return list(rows)


async def _ledger_sum_in(session, balance_id) -> Decimal:
    v = (await session.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(LedgerEntry.to_balance_id == balance_id)
    )).scalar_one()
    return Decimal(str(v))


async def _ledger_sum_out(session, balance_id) -> Decimal:
    v = (await session.execute(
        select(func.coalesce(func.sum(LedgerEntry.amount), 0)).where(LedgerEntry.from_balance_id == balance_id)
    )).scalar_one()
    return Decimal(str(v))


async def _balance_id(session, *, user_id=None, is_system=False, btype=BalanceType.WORK):
    stmt = select(Balance.id).where(Balance.type == btype, Balance.currency == Currency.USDT)
    if is_system:
        stmt = stmt.where(Balance.user_id.is_(None), Balance.is_system.is_(True))
    else:
        stmt = stmt.where(Balance.user_id == user_id)
    return (await session.execute(stmt)).scalar_one_or_none()


# ════════════════════════════════════════════════════════════════════════
# CORRECT BEHAVIOUR — settle credits the EXECUTOR, debits the REQUESTER
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_settle_credits_executor_debits_requester_exact_and_conserved(session):
    """On долив settle the доливщик (executor) is CREDITED amount + reward and the
    requester is DEBITED amount + price. Asserts exact balances AND money
    conservation: what permanently LEAVES the requester (amount + price) equals
    what reaches the executor (amount + reward) plus the platform profit
    (price − reward). Долив amount=100 fiat @rate 100 → 1.0 USDT; price 10% =
    0.10; reward 5% = 0.05."""
    requester = await _mk_user(session, username="req")
    executor = await _mk_user(session, username="exe")
    await _mk_balance(session, "1000", user_id=requester.id)
    await _mk_rate(session, rate="100")
    await _configure(session, executor_ids=str(executor.id), price="10", reward="5")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4000")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))

    amount_usdt = Decimal(str(doliv.amount_usdt))
    price_usdt = Decimal(str(doliv.doliv_price_usdt))
    reward_usdt = Decimal(str(doliv.trader_fee_usdt))
    assert amount_usdt == Decimal("1.0000")
    assert price_usdt == Decimal("0.1000")
    assert reward_usdt == Decimal("0.0500")

    # After freeze: requester WORK debited amount+price, parked in ESCROW.
    assert await _bal(session, user_id=requester.id) == Decimal("1000") - (amount_usdt + price_usdt)
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == amount_usdt + price_usdt

    await svc.claim(executor, str(doliv.uuid))
    done = await svc.execute(executor, str(doliv.uuid), receipt_content=_PDF, receipt_filename="receipt.pdf")
    assert done.status == PayoutStatus.COMPLETED

    # ── Executor CREDITED amount + reward; requester ESCROW fully drained. ──
    assert await _bal(session, user_id=executor.id) == amount_usdt + reward_usdt        # 1.05
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == Decimal("0.0000")
    # Requester WORK never returns — they paid amount + price permanently.
    assert await _bal(session, user_id=requester.id) == Decimal("1000") - (amount_usdt + price_usdt)
    # Platform profit = price − reward.
    assert await _bal(session, is_system=True) == price_usdt - reward_usdt              # 0.05

    # ── Conservation: requester out == executor in + platform in. ──
    requester_out = amount_usdt + price_usdt                                            # 1.10
    executor_in = amount_usdt + reward_usdt                                             # 1.05
    platform_in = price_usdt - reward_usdt                                             # 0.05
    assert requester_out == executor_in + platform_in

    # ── Ledger conservation cross-checked against the actual entries. ──
    req_escrow_id = await _balance_id(session, user_id=requester.id, btype=BalanceType.ESCROW)
    exec_work_id = await _balance_id(session, user_id=executor.id)
    system_id = await _balance_id(session, is_system=True)
    # Everything frozen into the requester ESCROW (1.10) leaves it again on settle.
    assert await _ledger_sum_in(session, req_escrow_id) == amount_usdt + price_usdt
    assert await _ledger_sum_out(session, req_escrow_id) == amount_usdt + price_usdt
    # The executor receives exactly amount (from requester) + reward (from system).
    assert await _ledger_sum_in(session, exec_work_id) == amount_usdt + reward_usdt
    # System receives price, pays out reward → nets price − reward.
    assert await _ledger_sum_in(session, system_id) - await _ledger_sum_out(session, system_id) == price_usdt - reward_usdt

    # Turnover filled by the FIAT amount (daily AND monthly), once.
    assert await _turnover(session, req.id) == (Decimal("4100.00"), Decimal("4100.00"))


@pytest.mark.asyncio
async def test_settle_ledger_has_exactly_three_legs_no_double_settle(session):
    """The долив settle writes EXACTLY three legs (amount→executor,
    price→system, reward→executor) under reference_id "doliv:<id>", on top of
    the single freeze leg. A double-settle bug would show as extra legs / doubled
    executor balance — re-running execute on a COMPLETED долив is rejected and
    leaves the leg count and balances untouched."""
    requester = await _mk_user(session, username="req")
    executor = await _mk_user(session, username="exe")
    await _mk_balance(session, "1000", user_id=requester.id)
    await _mk_rate(session, rate="100")
    await _configure(session, executor_ids=str(executor.id), price="10", reward="5")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))

    # After create: a single freeze leg (DOLIV).
    legs = await _ledger_legs(session, doliv.id)
    assert len(legs) == 1
    assert legs[0].reference_type == LedgerReferenceType.DOLIV

    await svc.claim(executor, str(doliv.uuid))
    await svc.execute(executor, str(doliv.uuid), receipt_content=_PDF, receipt_filename="receipt.pdf")

    legs = await _ledger_legs(session, doliv.id)
    # freeze(DOLIV) + settle{ amount(DOLIV), price(SYSTEM_COMMISSION), reward(TRADER_REWARD) } = 4
    assert len(legs) == 4
    type_counts = {}
    for leg in legs:
        type_counts[leg.reference_type] = type_counts.get(leg.reference_type, 0) + 1
    assert type_counts[LedgerReferenceType.DOLIV] == 2          # freeze + amount-to-executor
    assert type_counts[LedgerReferenceType.SYSTEM_COMMISSION] == 1
    assert type_counts[LedgerReferenceType.TRADER_REWARD] == 1

    executor_after = await _bal(session, user_id=executor.id)
    daily_after, _ = await _turnover(session, req.id)

    # SEQUENTIAL idempotency: a 2nd execute on the COMPLETED долив is rejected,
    # money moves ZERO more times, no extra legs, turnover filled once.
    with pytest.raises(DolivConflict):
        await svc.execute(executor, str(doliv.uuid), receipt_content=_PDF, receipt_filename="receipt.pdf")
    assert await _bal(session, user_id=executor.id) == executor_after
    assert len(await _ledger_legs(session, doliv.id)) == 4
    assert (await _turnover(session, req.id))[0] == daily_after == Decimal("5000.00")


@pytest.mark.asyncio
async def test_zero_price_zero_reward_still_conserves(session):
    """Edge: price% = reward% = 0 → the долив is a pure pass-through. Executor
    gets exactly the amount, requester pays exactly the amount, the platform
    nets nothing, and the optional price/reward legs are SKIPPED (settle only
    writes the legs that move money)."""
    requester = await _mk_user(session, username="req")
    executor = await _mk_user(session, username="exe")
    await _mk_balance(session, "500", user_id=requester.id)
    await _mk_rate(session, rate="50")
    await _configure(session, executor_ids=str(executor.id), price="0", reward="0")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="0")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))  # 100/50 = 2.0 usdt
    assert Decimal(str(doliv.doliv_price_usdt)) == Decimal("0.0000")
    assert Decimal(str(doliv.trader_fee_usdt)) == Decimal("0.0000")
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == Decimal("2.0000")

    await svc.claim(executor, str(doliv.uuid))
    await svc.execute(executor, str(doliv.uuid), receipt_content=_PDF, receipt_filename="receipt.pdf")

    assert await _bal(session, user_id=executor.id) == Decimal("2.0000")
    assert await _bal(session, user_id=requester.id) == Decimal("500") - Decimal("2.0000")
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == Decimal("0.0000")
    assert await _bal(session, is_system=True) == Decimal("0.0000")
    # Only the freeze leg + the single amount→executor settle leg exist.
    legs = await _ledger_legs(session, doliv.id)
    assert len(legs) == 2
    assert all(leg.reference_type == LedgerReferenceType.DOLIV for leg in legs)


@pytest.mark.asyncio
async def test_admin_cancel_refunds_full_freeze_no_executor_credit(session):
    """Admin-cancelling an UNCLAIMED долив (traders can't cancel) refunds the FULL
    freeze (amount + price) to the requester's WORK, credits no executor, and
    never touches turnover."""
    admin = await _mk_user(session, username="adm", role=UserRole.ADMIN)
    requester = await _mk_user(session, username="req")
    executor = await _mk_user(session, username="exe")
    await _mk_balance(session, "1000", user_id=requester.id)
    await _mk_rate(session, rate="100")
    await _configure(session, executor_ids=str(executor.id), price="10", reward="5")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))
    assert await _bal(session, user_id=requester.id) == Decimal("998.9000")

    out = await svc.admin_change_status(str(doliv.uuid), PayoutStatus.CANCELED, admin)
    assert out.status == PayoutStatus.CANCELED
    assert await _bal(session, user_id=requester.id) == Decimal("1000.0000")
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == Decimal("0.0000")
    assert await _bal(session, user_id=executor.id) == Decimal("0.0000")
    assert await _turnover(session, req.id) == (Decimal("4900.00"), Decimal("4900.00"))
    # freeze leg + refund leg, both DOLIV, net zero on the requester.
    assert len(await _ledger_legs(session, doliv.id)) == 2


# ════════════════════════════════════════════════════════════════════════
# ADVERSARIAL — pending доливы reduce remaining daily capacity (#24/#25)
# ════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_pending_doliv_reduces_remaining_second_over_cap_rejected(session):
    """A CREATED (pending) долив reduces the requisite's remaining daily
    capacity before its turnover is bumped. A second долив that would push past
    (limit_daily − turnover − pending) is rejected; the rejected create freezes
    NOTHING and writes no new долив."""
    requester = await _mk_user(session, username="req")
    await _mk_balance(session, "100000", user_id=requester.id)
    await _mk_rate(session, rate="1")  # 1 fiat == 1 usdt, keeps the math obvious
    await _configure(session, executor_ids="0", price="0", reward="0")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4000")
    # remaining = 5000 - 4000 = 1000

    svc = DolivService(session)
    d1 = await svc.create(requester, req.id, Decimal("700"))   # pending now 700, remaining 300
    assert d1.status == PayoutStatus.CREATED

    work_before = await _bal(session, user_id=requester.id)
    escrow_before = await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW)

    with pytest.raises(ValidationException):                    # 400 > remaining 300
        await svc.create(requester, req.id, Decimal("400"))

    # Rejection froze nothing and created no second долив.
    assert await _bal(session, user_id=requester.id) == work_before
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == escrow_before
    assert len(await svc.list_mine(requester)) == 1

    # The remaining 300 still fits.
    d2 = await svc.create(requester, req.id, Decimal("300"))
    assert d2.status == PayoutStatus.CREATED
    # Pending now 1000; even 1 more fiat over-commits the cap.
    with pytest.raises(ValidationException):
        await svc.create(requester, req.id, Decimal("1"))


@pytest.mark.asyncio
async def test_claimed_doliv_still_counts_as_pending(session):
    """A долив that is CLAIMED (taken, not yet executed) STILL counts as pending —
    its turnover is bumped only on execute. So a second долив is still capped by
    the claimed-but-unsettled amount."""
    requester = await _mk_user(session, username="req")
    executor = await _mk_user(session, username="exe")
    await _mk_balance(session, "100000", user_id=requester.id)
    await _mk_rate(session, rate="1")
    await _configure(session, executor_ids=str(executor.id), price="0", reward="0")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4500")
    # remaining 500

    svc = DolivService(session)
    d1 = await svc.create(requester, req.id, Decimal("400"))   # pending 400, remaining 100
    await svc.claim(executor, str(d1.uuid))                    # CLAIMED — still pending
    assert (await svc._get_doliv(str(d1.uuid))).status == PayoutStatus.CLAIMED

    with pytest.raises(ValidationException):                    # 200 > remaining 100
        await svc.create(requester, req.id, Decimal("200"))
    # Exactly the leftover 100 still fits while d1 is claimed-not-settled.
    ok = await svc.create(requester, req.id, Decimal("100"))
    assert ok.status == PayoutStatus.CREATED


@pytest.mark.asyncio
async def test_settled_doliv_frees_capacity_for_the_next(session):
    """Once a долив EXECUTES, its amount moves from `pending` into `turnover`
    (net remaining unchanged), but a CANCELED долив releases its pending hold
    entirely — freeing capacity the cap had reserved. Verifies the cap tracks
    pending correctly across both terminal transitions."""
    admin = await _mk_user(session, username="adm", role=UserRole.ADMIN)
    requester = await _mk_user(session, username="req")
    executor = await _mk_user(session, username="exe")
    await _mk_balance(session, "100000", user_id=requester.id)
    await _mk_rate(session, rate="1")
    await _configure(session, executor_ids=str(executor.id), price="0", reward="0")
    req = await _mk_requisite(session, requester.id, limit_daily="1000", daily_turnover="0")

    svc = DolivService(session)
    d1 = await svc.create(requester, req.id, Decimal("600"))   # pending 600, remaining 400
    d2 = await svc.create(requester, req.id, Decimal("400"))   # pending 1000, remaining 0
    with pytest.raises(ValidationException):                    # cap full
        await svc.create(requester, req.id, Decimal("1"))

    # Admin-cancel d2 → its 400 pending hold is released → 400 capacity returns.
    await svc.admin_change_status(str(d2.uuid), PayoutStatus.CANCELED, admin)
    # turnover still 0 (cancel never fills), pending back to 600 → remaining 400.
    assert await _turnover(session, req.id) == (Decimal("0.00"), Decimal("0.00"))
    d3 = await svc.create(requester, req.id, Decimal("400"))
    assert d3.status == PayoutStatus.CREATED

    # Execute d1 → 600 moves pending→turnover. Now turnover 600, pending 400 (d3),
    # remaining = 1000 - 600 - 400 = 0. One more cent is rejected.
    await svc.claim(executor, str(d1.uuid))
    await svc.execute(executor, str(d1.uuid), receipt_content=_PDF, receipt_filename="receipt.pdf")
    assert await _turnover(session, req.id) == (Decimal("600.00"), Decimal("600.00"))
    with pytest.raises(ValidationException):
        await svc.create(requester, req.id, Decimal("1"))


@pytest.mark.asyncio
async def test_doliv_capped_by_max_amount_config(session):
    """A долив above the configured max-amount is rejected (independent of the
    daily limit) and freezes nothing; one exactly at the max is allowed."""
    requester = await _mk_user(session, username="req")
    await _mk_balance(session, "100000", user_id=requester.id)
    await _mk_rate(session, rate="1")
    await _configure(session, executor_ids="0", price="0", reward="0", min_a="0", max_a="500")
    req = await _mk_requisite(session, requester.id, limit_daily="1000000", daily_turnover="0")

    svc = DolivService(session)
    with pytest.raises(ValidationException):                    # 501 > max 500
        await svc.create(requester, req.id, Decimal("501"))
    assert await svc.list_mine(requester) == []
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == Decimal("0.0000")

    ok = await svc.create(requester, req.id, Decimal("500"))   # exactly at max → allowed
    assert ok.status == PayoutStatus.CREATED


@pytest.mark.asyncio
async def test_doliv_exactly_at_remaining_allowed_one_over_rejected(session):
    """Boundary: a долив EXACTLY equal to the remaining daily capacity is allowed;
    one fiat cent over is rejected. (rate 1 → fiat == usdt so the boundary is
    crisp.)"""
    requester = await _mk_user(session, username="req")
    await _mk_balance(session, "100000", user_id=requester.id)
    await _mk_rate(session, rate="1")
    await _configure(session, executor_ids="0", price="0", reward="0")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")
    # remaining = exactly 100

    svc = DolivService(session)
    # One cent over the remaining is rejected, freezes nothing.
    with pytest.raises(ValidationException):
        await svc.create(requester, req.id, Decimal("100.01"))
    assert await svc.list_mine(requester) == []

    # Exactly the remaining is allowed.
    ok = await svc.create(requester, req.id, Decimal("100"))
    assert ok.status == PayoutStatus.CREATED
    # Now the cap is exactly full; even the smallest долив is rejected.
    with pytest.raises(ValidationException):
        await svc.create(requester, req.id, Decimal("0.01"))


@pytest.mark.asyncio
async def test_full_cap_rejected_before_any_freeze(session):
    """When turnover already equals the daily limit (remaining ≤ 0) every долив is
    rejected outright with the «лимит заполнен» guard — no freeze, no долив."""
    requester = await _mk_user(session, username="req")
    await _mk_balance(session, "100000", user_id=requester.id)
    await _mk_rate(session, rate="1")
    await _configure(session, executor_ids="0", price="0", reward="0")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="5000")

    svc = DolivService(session)
    with pytest.raises(ValidationException):
        await svc.create(requester, req.id, Decimal("1"))
    assert await svc.list_mine(requester) == []
    assert await _bal(session, user_id=requester.id) == Decimal("100000.0000")


@pytest.mark.asyncio
async def test_pending_cap_uses_fiat_amount_not_usdt(session):
    """The pending sum and the daily cap are both in FIAT, regardless of the
    rate. With rate 100, a долив's USDT amount (e.g. 7.0) is tiny next to its
    fiat amount (700) — the cap must compare fiat to fiat. A 400-fiat долив
    after a pending 700-fiat one (remaining 300) is rejected even though both
    USDT amounts are small."""
    requester = await _mk_user(session, username="req")
    await _mk_balance(session, "100000", user_id=requester.id)
    await _mk_rate(session, rate="100")  # fiat 700 → 7.0 usdt
    await _configure(session, executor_ids="0", price="0", reward="0")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4000")
    # remaining (fiat) = 1000

    svc = DolivService(session)
    d1 = await svc.create(requester, req.id, Decimal("700"))
    assert Decimal(str(d1.amount_usdt)) == Decimal("7.0000")    # usdt is small...
    assert Decimal(str(d1.amount)) == Decimal("700.0000")       # ...fiat is what the cap counts
    with pytest.raises(ValidationException):                     # 400 fiat > remaining 300 fiat
        await svc.create(requester, req.id, Decimal("400"))
    ok = await svc.create(requester, req.id, Decimal("300"))    # exactly the fiat leftover
    assert ok.status == PayoutStatus.CREATED
