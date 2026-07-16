"""Money-flow tests for DolivService (долив = requisite refill).

Exercises the full lifecycle against a real in-memory ledger:
  create (freeze requester) → claim (доливщик) → execute (settle + fill turnover)
plus cancel→refund and the guard rails (cap to remaining limit, min/max,
insufficient balance, ownership, executor ACL, missing rate). Asserts exact
balances and money conservation — the долив must neither mint nor burn USDT.

The долив is requisite-anchored: it targets a requisite directly and is priced
at the CURRENT platform rate for that requisite's currency (no order involved).
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.common.enums.cascading import RequisiteSource
from app.common.enums.rates import OrderBookSide
from app.common.enums.requisites import RequisiteStatus
from app.common.enums.users import UserRole
from app.core.exceptions import AppException
from app.modules.doliv.exceptions import DolivConflict, DolivForbidden
from app.modules.doliv.service import DolivService
from app.modules.finance.models import Balance
from app.modules.rates.models import RateConfig
from app.modules.requisites.models import Requisite, RequisiteLimit
from app.modules.settings.service import SettingsService
from app.modules.users.models import User

_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF"  # minimal valid PDF for execute receipts


# ─── helpers ───────────────────────────────────────────────────────────

async def _mk_user(session, username: str) -> User:
    u = User(username=username, password="x", role=UserRole.TRADER, is_system=False, is_blocked=False)
    session.add(u)
    await session.flush()
    return u


async def _set_work(session, user_id: int, amount: str) -> None:
    session.add(Balance(
        user_id=user_id, type=BalanceType.WORK, currency=Currency.USDT, amount=Decimal(amount),
    ))
    await session.flush()


async def _bal(session, user_id: int, btype: BalanceType) -> Decimal:
    res = await session.execute(
        select(Balance.amount).where(
            Balance.user_id == user_id, Balance.type == btype, Balance.currency == Currency.USDT,
        )
    )
    v = res.scalar_one_or_none()
    return Decimal(str(v)) if v is not None else Decimal("0")


async def _system_bal(session) -> Decimal:
    res = await session.execute(
        select(Balance.amount).where(
            Balance.user_id.is_(None), Balance.type == BalanceType.WORK, Balance.currency == Currency.USDT,
        )
    )
    v = res.scalar_one_or_none()
    return Decimal(str(v)) if v is not None else Decimal("0")


async def _mk_requisite(session, trader_id: int, *, limit_daily: str, daily_turnover: str) -> Requisite:
    req = Requisite(
        trader_id=trader_id, nickname="r", bank_name="Sber", account_number="40817000",
        account_holder="Ivan", payment_method=PaymentMethod.SBP,
        status=RequisiteStatus.ENABLED, currency=Currency.RUB, is_active=True,
        is_archived=False, source=RequisiteSource.LOCAL,
    )
    session.add(req)
    await session.flush()
    session.add(RequisiteLimit(
        requisite_id=req.id, limit_daily=Decimal(limit_daily), limit_monthly=Decimal("1000000"),
        current_daily_turnover=Decimal(daily_turnover), current_monthly_turnover=Decimal(daily_turnover),
    ))
    await session.flush()
    return req


async def _mk_rate(session, *, currency: Currency = Currency.RUB, rate: str = "100") -> None:
    """Active platform rate config — долив-create reads it for amount_usdt."""
    session.add(RateConfig(
        name="r", side=OrderBookSide.SELL, fiat_currency=currency,
        is_active=True, current_rate=float(rate),
    ))
    await session.flush()


async def _configure(session, *, executor_ids: str, price="10", reward="5", min_a="0", max_a="0") -> None:
    s = SettingsService(session)
    await s.set("doliv_executor_user_ids", executor_ids)
    await s.set("doliv_price_percent", price)
    await s.set("doliv_executor_reward_percent", reward)
    await s.set("doliv_min_amount", min_a)
    await s.set("doliv_max_amount", max_a)


# ─── tests ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_lifecycle_conserves_money_and_fills_turnover(session):
    requester = await _mk_user(session, "req")
    executor = await _mk_user(session, "exe")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session, rate="100")
    await _configure(session, executor_ids=str(executor.id), price="10", reward="5")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")

    svc = DolivService(session)

    # create долив for 100 fiat → 1.0 usdt; price 10% = 0.10; reward 5% = 0.05.
    doliv = await svc.create(requester, req.id, Decimal("100"))
    assert doliv.is_doliv is True
    assert doliv.status == PayoutStatus.CREATED
    assert doliv.refill_requisite_id == req.id
    assert doliv.refill_order_id is None
    assert Decimal(str(doliv.amount_usdt)) == Decimal("1.0000")
    assert Decimal(str(doliv.doliv_price_usdt)) == Decimal("0.1000")
    assert Decimal(str(doliv.trader_fee_usdt)) == Decimal("0.0500")  # executor reward
    # requester frozen amount + price (1.10): WORK 1000 → 998.90, ESCROW 1.10.
    assert await _bal(session, requester.id, BalanceType.WORK) == Decimal("998.9000")
    assert await _bal(session, requester.id, BalanceType.ESCROW) == Decimal("1.1000")

    # доливщик claims and executes.
    claimed = await svc.claim(executor, str(doliv.uuid))
    assert claimed.status == PayoutStatus.CLAIMED
    assert claimed.trader_id == executor.id

    done = await svc.execute(executor, str(doliv.uuid), receipt_content=_PDF, receipt_filename="receipt.pdf")
    assert done.status == PayoutStatus.COMPLETED

    # Money: requester ESCROW drained; доливщик +amount+reward; system +(price−reward).
    assert await _bal(session, requester.id, BalanceType.ESCROW) == Decimal("0.0000")
    assert await _bal(session, requester.id, BalanceType.WORK) == Decimal("998.9000")
    assert await _bal(session, executor.id, BalanceType.WORK) == Decimal("1.0500")  # 1.0 + 0.05
    assert await _system_bal(session) == Decimal("0.0500")  # 0.10 price − 0.05 reward

    # Conservation: requester out 1.10 == доливщик in 1.05 + system in 0.05.
    assert Decimal("1.1000") == Decimal("1.0500") + Decimal("0.0500")

    # Requisite turnover filled by the fiat amount (daily AND monthly).
    lim = (await session.execute(
        select(RequisiteLimit).where(RequisiteLimit.requisite_id == req.id)
    )).scalar_one()
    await session.refresh(lim)
    assert Decimal(str(lim.current_daily_turnover)) == Decimal("5000.00")
    assert Decimal(str(lim.current_monthly_turnover)) == Decimal("5000.00")


@pytest.mark.asyncio
async def test_list_for_executor_shows_pool_and_mine_gated(session):
    """The «Долив» tab data: an open долив is visible to доливщики (pool) and
    stays visible to the one who claimed it (their work + history); non-executors
    and the requester (if not a доливщик) see nothing."""
    requester = await _mk_user(session, "req")
    executor = await _mk_user(session, "exe")
    intruder = await _mk_user(session, "intruder")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session)
    await _configure(session, executor_ids=str(executor.id))
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))

    # Open pool долив: visible to the доливщик, hidden for everyone else.
    assert [p.id for p in await svc.list_for_executor(executor)] == [doliv.id]
    assert await svc.list_for_executor(intruder) == []
    assert await svc.list_for_executor(requester) == []  # requester isn't a доливщик

    await svc.claim(executor, str(doliv.uuid))
    # Still in the доливщик's list after claim (their in-progress work / history).
    rows = await svc.list_for_executor(executor)
    assert [p.id for p in rows] == [doliv.id]
    assert rows[0].status == PayoutStatus.CLAIMED


@pytest.mark.asyncio
async def test_list_all_and_amount_bounds_for_admin(session):
    """list_all returns every долив (admin view) regardless of executor; the
    response carries requester/executor ids; amount_bounds echoes the settings."""
    requester = await _mk_user(session, "req")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session)
    await _configure(session, executor_ids="0", min_a="5", max_a="500")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))

    all_rows = await svc.list_all()
    assert [p.id for p in all_rows] == [doliv.id]
    assert all_rows[0].requester_trader_id == requester.id

    cfg_min, cfg_max, price_pct = await svc.amount_bounds()
    assert (cfg_min, cfg_max, price_pct) == (Decimal("5"), Decimal("500"), Decimal("10"))


@pytest.mark.asyncio
async def test_amount_above_remaining_limit_rejected(session):
    requester = await _mk_user(session, "req")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session)
    await _configure(session, executor_ids="0")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")

    svc = DolivService(session)
    with pytest.raises(AppException):  # remaining is 100; 101 exceeds it
        await svc.create(requester, req.id, Decimal("101"))
    # Nothing frozen.
    assert await _bal(session, requester.id, BalanceType.WORK) == Decimal("1000.0000")


@pytest.mark.asyncio
async def test_insufficient_balance_rejected_no_row(session):
    requester = await _mk_user(session, "req")
    await _set_work(session, requester.id, "0.50")  # < amount(1.0)+price(0.1)
    await _mk_rate(session)
    await _configure(session, executor_ids="0", price="10")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")

    svc = DolivService(session)
    with pytest.raises(AppException):
        await svc.create(requester, req.id, Decimal("100"))
    # No долив persisted, nothing frozen.
    assert await svc.list_mine(requester) == []
    assert await _bal(session, requester.id, BalanceType.WORK) == Decimal("0.5000")
    assert await _bal(session, requester.id, BalanceType.ESCROW) == Decimal("0.0000")


@pytest.mark.asyncio
async def test_create_rejects_foreign_requisite(session):
    requester = await _mk_user(session, "req")
    other = await _mk_user(session, "other")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session)
    await _configure(session, executor_ids="0")
    req = await _mk_requisite(session, other.id, limit_daily="5000", daily_turnover="0")  # belongs to `other`

    svc = DolivService(session)
    with pytest.raises(DolivForbidden):
        await svc.create(requester, req.id, Decimal("100"))


@pytest.mark.asyncio
async def test_create_without_active_rate_rejected(session):
    """No active rate config for the requisite's currency → create refuses
    (можно посчитать amount_usdt только по курсу), nothing frozen."""
    requester = await _mk_user(session, "req")
    await _set_work(session, requester.id, "1000")
    await _configure(session, executor_ids="0")  # NB: no _mk_rate
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="0")

    svc = DolivService(session)
    with pytest.raises(AppException):
        await svc.create(requester, req.id, Decimal("100"))
    assert await _bal(session, requester.id, BalanceType.WORK) == Decimal("1000.0000")


@pytest.mark.asyncio
async def test_non_executor_cannot_claim(session):
    requester = await _mk_user(session, "req")
    executor = await _mk_user(session, "exe")
    intruder = await _mk_user(session, "intruder")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session)
    await _configure(session, executor_ids=str(executor.id))  # intruder not listed
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))
    with pytest.raises(DolivForbidden):
        await svc.claim(intruder, str(doliv.uuid))
    # Pool is empty for a non-executor.
    assert await svc.list_pool(intruder) == []
    assert len(await svc.list_pool(executor)) == 1


@pytest.mark.asyncio
async def test_execute_requires_claimed_status(session):
    requester = await _mk_user(session, "req")
    executor = await _mk_user(session, "exe")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session)
    await _configure(session, executor_ids=str(executor.id))
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))
    # Not claimed yet → execute is a conflict, no money moves.
    with pytest.raises(DolivConflict):
        await svc.execute(executor, str(doliv.uuid), receipt_content=_PDF, receipt_filename="receipt.pdf")
    assert await _bal(session, executor.id, BalanceType.WORK) == Decimal("0.0000")


@pytest.mark.asyncio
async def test_double_execute_is_rejected(session):
    requester = await _mk_user(session, "req")
    executor = await _mk_user(session, "exe")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session)
    await _configure(session, executor_ids=str(executor.id), reward="5")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))
    await svc.claim(executor, str(doliv.uuid))
    await svc.execute(executor, str(doliv.uuid), receipt_content=_PDF, receipt_filename="receipt.pdf")
    after_first = await _bal(session, executor.id, BalanceType.WORK)
    # A second execute on the COMPLETED долив is rejected — no double settle / turnover.
    with pytest.raises(DolivConflict):
        await svc.execute(executor, str(doliv.uuid), receipt_content=_PDF, receipt_filename="receipt.pdf")
    assert await _bal(session, executor.id, BalanceType.WORK) == after_first

    lim = (await session.execute(
        select(RequisiteLimit).where(RequisiteLimit.requisite_id == req.id)
    )).scalar_one()
    await session.refresh(lim)
    assert Decimal(str(lim.current_daily_turnover)) == Decimal("5000.00")  # filled once, not twice


@pytest.mark.asyncio
async def test_amount_outside_min_max_rejected(session):
    requester = await _mk_user(session, "req")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session)
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="0")
    svc = DolivService(session)

    await _configure(session, executor_ids="0", min_a="200", max_a="0")
    with pytest.raises(AppException):  # below min
        await svc.create(requester, req.id, Decimal("100"))

    await _configure(session, executor_ids="0", min_a="0", max_a="50")
    with pytest.raises(AppException):  # above max
        await svc.create(requester, req.id, Decimal("100"))


@pytest.mark.asyncio
async def test_pending_dolivs_count_against_daily_limit(session):
    """A долив's amount counts against the requisite's remaining daily limit
    until it settles (turnover is bumped only on execute) — so a second долив
    that would over-commit the cap while the first is still pending is rejected.
    Closes the concurrent over-commit gap (#24/#25)."""
    requester = await _mk_user(session, "req")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session)
    await _configure(session, executor_ids="0")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="4900")  # remaining 100

    svc = DolivService(session)
    await svc.create(requester, req.id, Decimal("70"))      # 70 ≤ 100 → OK, now pending=70
    with pytest.raises(AppException):                       # remaining now 30; 40 > 30 → rejected
        await svc.create(requester, req.id, Decimal("40"))
    ok = await svc.create(requester, req.id, Decimal("30"))  # fits the leftover
    assert ok.status == PayoutStatus.CREATED


@pytest.mark.asyncio
async def test_execute_stores_receipt_and_pushes_to_requester_bot(session):
    """Executing a долив stores the доливщик's receipt on the долив and fires the
    bot-notify task targeting the долив id (delivered to the REQUESTER's bot)."""
    requester = await _mk_user(session, "req")
    executor = await _mk_user(session, "exe")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session, rate="100")
    await _configure(session, executor_ids=str(executor.id))
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="0")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))
    await svc.claim(executor, str(doliv.uuid))

    with patch("app.modules.doliv.service.celery_app") as mock_celery:
        done = await svc.execute(executor, str(doliv.uuid), receipt_content=_PDF, receipt_filename="r.pdf")

    import os
    assert done.status == PayoutStatus.COMPLETED
    assert done.receipt_file and os.path.isfile(done.receipt_file)  # saved within UPLOAD_DIR
    assert done.receipt_uploaded_at is not None
    mock_celery.send_task.assert_called_once_with(
        "app.workers.tasks.trader_bot.notify_requester_doliv_receipt",
        args=[done.id],
    )


@pytest.mark.asyncio
async def test_receipt_download_access_requester_and_executor_only(session):
    """Only the requester or the доливщик may pull the долив receipt file."""
    requester = await _mk_user(session, "req")
    executor = await _mk_user(session, "exe")
    intruder = await _mk_user(session, "intruder")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session, rate="100")
    await _configure(session, executor_ids=str(executor.id))
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="0")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))
    await svc.claim(executor, str(doliv.uuid))
    # execute saves the receipt under the (test) UPLOAD_DIR → containment guard passes.
    with patch("app.modules.doliv.service.celery_app"):
        await svc.execute(executor, str(doliv.uuid), receipt_content=_PDF, receipt_filename="r.pdf")

    assert (await svc.get_with_receipt_access(str(doliv.uuid), requester)).id == doliv.id
    assert (await svc.get_with_receipt_access(str(doliv.uuid), executor)).id == doliv.id
    with pytest.raises(DolivForbidden):
        await svc.get_with_receipt_access(str(doliv.uuid), intruder)


@pytest.mark.asyncio
async def test_trader_cancel_created_refunds(session):
    """The requester can cancel a CREATED долив → full refund; a non-owner can't."""
    requester = await _mk_user(session, "req")
    intruder = await _mk_user(session, "intruder")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session, rate="100")
    await _configure(session, executor_ids="0")
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="0")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))
    assert await _bal(session, requester.id, BalanceType.WORK) == Decimal("998.9000")  # frozen 1.10

    with pytest.raises(DolivForbidden):
        await svc.cancel(intruder, str(doliv.uuid))

    out = await svc.cancel(requester, str(doliv.uuid))
    assert out.status == PayoutStatus.CANCELED
    assert await _bal(session, requester.id, BalanceType.WORK) == Decimal("1000.0000")
    assert await _bal(session, requester.id, BalanceType.ESCROW) == Decimal("0.0000")


@pytest.mark.asyncio
async def test_trader_cannot_cancel_claimed_doliv(session):
    """Once a доливщик takes the долив («В работе» / CLAIMED) the trader can't cancel."""
    requester = await _mk_user(session, "req")
    executor = await _mk_user(session, "exe")
    await _set_work(session, requester.id, "1000")
    await _mk_rate(session, rate="100")
    await _configure(session, executor_ids=str(executor.id))
    req = await _mk_requisite(session, requester.id, limit_daily="5000", daily_turnover="0")

    svc = DolivService(session)
    doliv = await svc.create(requester, req.id, Decimal("100"))
    await svc.claim(executor, str(doliv.uuid))
    with pytest.raises(DolivConflict):
        await svc.cancel(requester, str(doliv.uuid))
