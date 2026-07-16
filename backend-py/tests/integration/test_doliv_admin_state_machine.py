"""
Integration tests for the ADMIN долив STATE MACHINE
(``DolivService.admin_change_status``) against a REAL in-memory ledger.

Admin can move a долив to ANY status; the state machine maps each status to a
money-state (FROZEN / SETTLED / REFUNDED), normalises the CURRENT money back to
the frozen baseline (reverse a settle, or re-freeze a refund), then applies the
TARGET money-state — so any status→status move moves money EXACTLY once and never
mints/loses it. These tests pin the exact Decimal balances, ledger legs, requisite
turnover, conservation, idempotency, the executor-required guards, and the
adversarial rollbacks (можно't un-complete if the доливщик spent the funds; can't
re-freeze if the requester lacks WORK). SQLite has no real row locks, so
idempotency is proven SEQUENTIALLY (the app-level re-check the lock protects).

Numbers: amount 1000 fiat @ rate 100 → amount_usdt 10; price 10% → 1.0; executor
reward 5% → 0.5. freeze = 11 (requester WORK→ESCROW); settle → executor 10.5,
system 0.5, requester ESCROW 0, turnover += 1000.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from app.common.enums.balances import BalanceType
from app.common.enums.cascading import RequisiteSource
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.common.enums.rates import OrderBookSide
from app.common.enums.requisites import RequisiteStatus
from app.common.enums.users import UserRole
from app.core.exceptions import ValidationException
from app.modules.doliv.exceptions import DolivConflict
from app.modules.doliv.service import DolivService
from app.modules.finance.models import Balance
from app.modules.payouts.models import Payout
from app.modules.rates.models import RateConfig
from app.modules.requisites.models import Requisite, RequisiteLimit
from app.modules.settings.service import SettingsService
from app.modules.users.models import User

AMT = Decimal("1000")        # fiat
AMT_USDT = Decimal("10.0000")
PRICE = Decimal("1.0000")    # 10% of 10
REWARD = Decimal("0.5000")   # 5% of 10
FREEZE = AMT_USDT + PRICE    # 11
START_WORK = Decimal("50.0000")
LIMIT_DAILY = Decimal("1000000")


@pytest.fixture(autouse=True)
def _stub_celery():
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


# ── builders ───────────────────────────────────────────────────────────────


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


async def _mk_requisite(session, trader_id) -> Requisite:
    req = Requisite(
        trader_id=trader_id, nickname="r", bank_name="Sber", account_number="40817000",
        account_holder="Ivan", payment_method=PaymentMethod.SBP,
        status=RequisiteStatus.ENABLED, currency=Currency.RUB, is_active=True,
        is_archived=False, source=RequisiteSource.LOCAL,
    )
    session.add(req)
    await session.flush()
    session.add(RequisiteLimit(
        requisite_id=req.id, limit_daily=LIMIT_DAILY, limit_monthly=Decimal("100000000"),
        current_daily_turnover=Decimal("0"), current_monthly_turnover=Decimal("0"),
    ))
    await session.flush()
    return req


async def _mk_rate(session, *, currency=Currency.RUB, rate="100") -> None:
    session.add(RateConfig(
        name="r", side=OrderBookSide.SELL, fiat_currency=currency,
        is_active=True, current_rate=float(rate),
    ))
    await session.flush()


async def _configure(session, *, executor_ids: str) -> None:
    s = SettingsService(session)
    await s.set("doliv_executor_user_ids", executor_ids)
    await s.set("doliv_price_percent", "10")
    await s.set("doliv_executor_reward_percent", "5")
    await s.set("doliv_min_amount", "0")
    await s.set("doliv_max_amount", "0")


# ── readers ──────────────────────────────────────────────────────────────


async def _bal(session, *, user_id=None, is_system=False, btype=BalanceType.WORK) -> Decimal:
    stmt = select(Balance.amount).where(Balance.type == btype, Balance.currency == Currency.USDT)
    if is_system:
        stmt = stmt.where(Balance.user_id.is_(None), Balance.is_system.is_(True))
    else:
        stmt = stmt.where(Balance.user_id == user_id)
    v = (await session.execute(stmt)).scalar_one_or_none()
    return Decimal(str(v)) if v is not None else Decimal("0")


async def _turnover(session, requisite_id) -> tuple:
    """(daily, monthly) — admin_change_status moves BOTH counters, so assert both."""
    lim = (await session.execute(
        select(RequisiteLimit).where(RequisiteLimit.requisite_id == requisite_id)
    )).scalar_one()
    return (Decimal(str(lim.current_daily_turnover)), Decimal(str(lim.current_monthly_turnover)))


async def _status(session, payout_id) -> PayoutStatus:
    return (await session.execute(select(Payout.status).where(Payout.id == payout_id))).scalar_one()


async def _setup(session):
    """requester + executor + a claimable долив. Returns (admin, requester,
    executor, requisite, payout)."""
    admin = await _mk_user(session, username="adm", role=UserRole.ADMIN)
    requester = await _mk_user(session, username="req")
    executor = await _mk_user(session, username="exe")
    await _mk_balance(session, START_WORK, user_id=requester.id)
    req = await _mk_requisite(session, requester.id)
    await _configure(session, executor_ids=str(executor.id))
    await _mk_rate(session)
    svc = DolivService(session)
    payout = await svc.create(requester, req.id, AMT)        # CREATED, frozen 11
    return admin, requester, executor, req, payout


# ── correct transitions ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claimed_to_completed_settles_and_bumps_turnover(session):
    admin, requester, executor, req, payout = await _setup(session)
    svc = DolivService(session)
    await svc.claim(executor, str(payout.uuid))             # CLAIMED

    out = await svc.admin_change_status(str(payout.uuid), PayoutStatus.COMPLETED, admin)

    assert out.status == PayoutStatus.COMPLETED
    assert await _bal(session, user_id=executor.id) == AMT_USDT + REWARD       # 10.5
    assert await _bal(session, user_id=requester.id) == START_WORK - FREEZE    # 39 (frozen spent)
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, is_system=True) == PRICE - REWARD               # 0.5
    assert await _turnover(session, req.id) == (AMT, AMT)                             # +1000


@pytest.mark.asyncio
async def test_completed_to_canceled_reverses_then_refunds(session):
    admin, requester, executor, req, payout = await _setup(session)
    svc = DolivService(session)
    await svc.claim(executor, str(payout.uuid))
    await svc.admin_change_status(str(payout.uuid), PayoutStatus.COMPLETED, admin)

    out = await svc.admin_change_status(str(payout.uuid), PayoutStatus.CANCELED, admin)

    assert out.status == PayoutStatus.CANCELED
    # Executor fully clawed back, system net 0, requester whole again, turnover back.
    assert await _bal(session, user_id=executor.id) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")
    assert await _bal(session, user_id=requester.id) == START_WORK            # 50 — refunded
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _turnover(session, req.id) == (Decimal("0"), Decimal("0"))


@pytest.mark.asyncio
async def test_completed_to_created_reverses_to_frozen_and_clears_executor(session):
    admin, requester, executor, req, payout = await _setup(session)
    svc = DolivService(session)
    await svc.claim(executor, str(payout.uuid))
    await svc.admin_change_status(str(payout.uuid), PayoutStatus.COMPLETED, admin)

    out = await svc.admin_change_status(str(payout.uuid), PayoutStatus.CREATED, admin)

    assert out.status == PayoutStatus.CREATED
    assert out.trader_id is None and out.claimed_at is None      # back to unclaimed pool
    # Money back to the frozen baseline.
    assert await _bal(session, user_id=executor.id) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")
    assert await _bal(session, user_id=requester.id) == START_WORK - FREEZE   # 39
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == FREEZE  # 11 re-frozen
    assert await _turnover(session, req.id) == (Decimal("0"), Decimal("0"))


@pytest.mark.asyncio
async def test_created_to_canceled_refunds(session):
    admin, requester, executor, req, payout = await _setup(session)
    out = await DolivService(session).admin_change_status(str(payout.uuid), PayoutStatus.CANCELED, admin)
    assert out.status == PayoutStatus.CANCELED
    assert await _bal(session, user_id=requester.id) == START_WORK           # fully refunded
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == Decimal("0")


@pytest.mark.asyncio
async def test_canceled_to_created_refreezes(session):
    admin, requester, executor, req, payout = await _setup(session)
    svc = DolivService(session)
    await svc.admin_change_status(str(payout.uuid), PayoutStatus.CANCELED, admin)   # refunded → WORK 50

    out = await svc.admin_change_status(str(payout.uuid), PayoutStatus.CREATED, admin)

    assert out.status == PayoutStatus.CREATED
    assert await _bal(session, user_id=requester.id) == START_WORK - FREEZE          # 39 re-frozen
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == FREEZE


# ── idempotency / guards ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_same_status_is_noop(session):
    admin, requester, executor, req, payout = await _setup(session)
    svc = DolivService(session)
    await svc.claim(executor, str(payout.uuid))
    await svc.admin_change_status(str(payout.uuid), PayoutStatus.COMPLETED, admin)
    exec_after = await _bal(session, user_id=executor.id)

    # Re-issuing COMPLETED moves no money again.
    await svc.admin_change_status(str(payout.uuid), PayoutStatus.COMPLETED, admin)
    assert await _bal(session, user_id=executor.id) == exec_after
    assert await _turnover(session, req.id) == (AMT, AMT)          # not double-bumped


@pytest.mark.asyncio
async def test_completed_then_completed_does_not_double_settle(session):
    """COMPLETED→CANCELED→COMPLETED: settle happens once per entry, money exact."""
    admin, requester, executor, req, payout = await _setup(session)
    svc = DolivService(session)
    await svc.claim(executor, str(payout.uuid))
    await svc.admin_change_status(str(payout.uuid), PayoutStatus.COMPLETED, admin)
    await svc.admin_change_status(str(payout.uuid), PayoutStatus.CANCELED, admin)
    await svc.admin_change_status(str(payout.uuid), PayoutStatus.COMPLETED, admin)

    assert await _bal(session, user_id=executor.id) == AMT_USDT + REWARD     # 10.5 exactly once
    assert await _bal(session, is_system=True) == PRICE - REWARD
    assert await _turnover(session, req.id) == (AMT, AMT)


@pytest.mark.asyncio
async def test_to_completed_without_executor_rejected(session):
    admin, requester, executor, req, payout = await _setup(session)   # CREATED, never claimed
    with pytest.raises(DolivConflict):
        await DolivService(session).admin_change_status(str(payout.uuid), PayoutStatus.COMPLETED, admin)
    assert await _status(session, payout.id) == PayoutStatus.CREATED


@pytest.mark.asyncio
async def test_to_claimed_without_executor_rejected(session):
    admin, requester, executor, req, payout = await _setup(session)
    with pytest.raises(DolivConflict):
        await DolivService(session).admin_change_status(str(payout.uuid), PayoutStatus.CLAIMED, admin)


# ── adversarial rollbacks ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_uncomplete_after_executor_spent_rolls_back(session):
    """Can't un-complete if the доливщик already spent the settled funds — the
    reverse legs raise Insufficient funds and the WHOLE transition rolls back."""
    admin, requester, executor, req, payout = await _setup(session)
    svc = DolivService(session)
    await svc.claim(executor, str(payout.uuid))
    await svc.admin_change_status(str(payout.uuid), PayoutStatus.COMPLETED, admin)

    # Executor spends the settled funds (WORK → 0).
    exec_work = (await session.execute(
        select(Balance).where(Balance.user_id == executor.id, Balance.type == BalanceType.WORK)
    )).scalar_one()
    exec_work.amount = Decimal("0")
    await session.flush()

    with pytest.raises(ValidationException):
        await svc.admin_change_status(str(payout.uuid), PayoutStatus.CREATED, admin)

    # Nothing changed: still COMPLETED, turnover still filled, requester not credited.
    assert await _status(session, payout.id) == PayoutStatus.COMPLETED
    assert await _turnover(session, req.id) == (AMT, AMT)
    assert await _bal(session, user_id=requester.id, btype=BalanceType.ESCROW) == Decimal("0")


@pytest.mark.asyncio
async def test_refreeze_without_requester_funds_rolls_back(session):
    """Can't re-freeze (REFUNDED→FROZEN) if the requester spent the refund."""
    admin, requester, executor, req, payout = await _setup(session)
    svc = DolivService(session)
    await svc.admin_change_status(str(payout.uuid), PayoutStatus.CANCELED, admin)   # refund → WORK 50

    req_work = (await session.execute(
        select(Balance).where(Balance.user_id == requester.id, Balance.type == BalanceType.WORK)
    )).scalar_one()
    req_work.amount = Decimal("0")           # spent it
    await session.flush()

    with pytest.raises(ValidationException):
        await svc.admin_change_status(str(payout.uuid), PayoutStatus.CREATED, admin)
    assert await _status(session, payout.id) == PayoutStatus.CANCELED


# ── conservation ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_value_conserved_across_full_status_tour(session):
    """The grand total of USDT across every balance is invariant no matter how
    the admin drives the долив around the status graph."""
    admin, requester, executor, req, payout = await _setup(session)
    svc = DolivService(session)

    async def _grand_total() -> Decimal:
        rows = (await session.execute(select(Balance.amount).where(Balance.currency == Currency.USDT))).scalars().all()
        return sum((Decimal(str(a)) for a in rows), Decimal("0"))

    seeded = await _grand_total()                      # == START_WORK (50)
    await svc.claim(executor, str(payout.uuid))
    for target in (
        PayoutStatus.COMPLETED, PayoutStatus.CANCELED, PayoutStatus.CREATED,
    ):
        # CREATED clears the executor; re-claim so COMPLETED stays reachable.
        await svc.admin_change_status(str(payout.uuid), target, admin)
        assert await _grand_total() == seeded
        if target == PayoutStatus.CREATED:
            await svc.claim(executor, str(payout.uuid))
    assert seeded == START_WORK
