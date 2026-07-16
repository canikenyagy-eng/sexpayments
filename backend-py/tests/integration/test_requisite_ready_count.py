"""Integration tests for the «can receive a payin RIGHT NOW» requisite count
(sidebar «Реквизиты» badge), which must mirror the amount-independent pooling
gates — static state AND capacity (concurrency + daily/monthly headroom).
Real in-memory Postgres session.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.common.enums.cascading import RequisiteSource
from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.receipt_moderations import ModerationStatus
from app.common.enums.requisites import RequisiteStatus
from app.common.enums.traders import TraderStatus
from app.common.enums.users import UserRole
from app.common.enums.balances import BalanceType
from app.core.security import get_password_hash
from app.modules.finance.models import Balance
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.requisites.models import Requisite, RequisiteLimit
from app.modules.stats.repository import StatsRepository
from app.modules.traders.models import Trader
from app.modules.users.models import User


async def _mk_trader(session, *, payin_active=True, work_balance="100") -> User:
    u = User(
        username=f"tr_{uuid4().hex[:6]}", password=get_password_hash("pass12345"),
        role=UserRole.TRADER, totp_enabled=False, is_blocked=False, use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    session.add(Trader(user_id=u.id, status=TraderStatus.ENABLED, is_payin_active=payin_active))
    session.add(Balance(
        user_id=u.id, is_system=False, type=BalanceType.WORK,
        currency=Currency.USDT, amount=Decimal(work_balance),
    ))
    await session.flush()
    return u


async def _mk_requisite(
    session, *, trader_user_id, is_active=True, is_archived=False,
    status=RequisiteStatus.ENABLED, source=RequisiteSource.LOCAL,
    limit_daily="100000", current_daily="0", limit_min="100",
    max_concurrent=None,
) -> Requisite:
    r = Requisite(
        trader_id=trader_user_id, bank_name="Bank", account_number=uuid4().hex[:12],
        account_holder="Holder", payment_method=PaymentMethod.SBP,
        status=status, currency=Currency.RUB, is_active=is_active,
        is_archived=is_archived, source=source,
    )
    session.add(r)
    await session.flush()
    session.add(RequisiteLimit(
        requisite_id=r.id, limit_daily=Decimal(limit_daily), limit_monthly=Decimal("100000000"),
        limit_min_transaction=Decimal(limit_min), limit_max_transaction=Decimal("50000"),
        limit_max_concurrent_orders=max_concurrent,
        current_daily_turnover=Decimal(current_daily), current_monthly_turnover=Decimal("0"),
    ))
    await session.flush()
    return r


async def _mk_active_order(session, *, requisite_id, amount="500") -> Order:
    n = uuid4().hex[:8]
    owner = User(username=f"mo_{n}", password=get_password_hash("p"), role=UserRole.MERCHANT,
                 totp_enabled=False, is_blocked=False, use_shared_balance=True)
    session.add(owner)
    await session.flush()
    m = Merchant(user_id=owner.id, api_key=f"k-{n}", api_secret=f"s-{n}", currency=Currency.RUB, fees={})
    session.add(m)
    await session.flush()
    o = Order(
        external_id=f"ext-{n}", merchant_id=m.id, requisite_id=requisite_id,
        direction=PaymentDirection.PAYIN, payment_method=PaymentMethod.SBP,
        amount=Decimal(amount), currency=Currency.RUB, status=OrderStatus.PENDING,
        moderation_status=ModerationStatus.NONE,
    )
    session.add(o)
    await session.flush()
    return o


@pytest.mark.asyncio
async def test_ready_count_includes_only_capacity_available_requisites(session):
    trader = await _mk_trader(session)
    repo = StatsRepository(session)

    # 1. Healthy → counted.
    await _mk_requisite(session, trader_user_id=trader.id)
    # 2. is_active=False → excluded (the runtime toggle the pooler honours).
    await _mk_requisite(session, trader_user_id=trader.id, is_active=False)
    # 3. status=DISABLED → excluded.
    await _mk_requisite(session, trader_user_id=trader.id, status=RequisiteStatus.DISABLED)
    # 4. archived → excluded.
    await _mk_requisite(session, trader_user_id=trader.id, is_archived=True)
    # 5. cascade source → excluded.
    await _mk_requisite(session, trader_user_id=trader.id, source=RequisiteSource.CASCADE)
    # 6. daily limit full (950 + min 100 > 1000) → excluded.
    await _mk_requisite(session, trader_user_id=trader.id, limit_daily="1000", current_daily="950", limit_min="100")
    # 7. concurrency cap reached (cap=1 with one active order) → excluded.
    full = await _mk_requisite(session, trader_user_id=trader.id, max_concurrent=1)
    await _mk_active_order(session, requisite_id=full.id)

    count = await repo.count_traffic_accepting_requisites_for_trader(trader.id)
    assert count == 1  # only the healthy requisite can take a deal right now


@pytest.mark.asyncio
async def test_ready_count_excludes_all_when_trader_has_no_balance(session):
    trader = await _mk_trader(session, work_balance="0")
    await _mk_requisite(session, trader_user_id=trader.id)
    await _mk_requisite(session, trader_user_id=trader.id)

    count = await StatsRepository(session).count_traffic_accepting_requisites_for_trader(trader.id)
    assert count == 0  # zero WORK balance → cannot cover any order


@pytest.mark.asyncio
async def test_ready_count_excludes_when_payin_inactive(session):
    trader = await _mk_trader(session, payin_active=False)
    await _mk_requisite(session, trader_user_id=trader.id)

    count = await StatsRepository(session).count_traffic_accepting_requisites_for_trader(trader.id)
    assert count == 0


@pytest.mark.asyncio
async def test_list_state_and_ready_only_filters(session):
    from app.modules.requisites.repository import RequisiteRepository

    trader = await _mk_trader(session)
    healthy = await _mk_requisite(session, trader_user_id=trader.id)
    off = await _mk_requisite(session, trader_user_id=trader.id, is_active=False)
    arch = await _mk_requisite(session, trader_user_id=trader.id, is_archived=True)
    repo = RequisiteRepository(session)

    # state=active → only enabled AND is_active AND not archived
    active_items, _ = await repo.get_by_trader(trader.id, state="active")
    assert {r.id for r in active_items} == {healthy.id}

    # state=archived → opts into archived rows
    arch_items, _ = await repo.get_by_trader(trader.id, state="archived")
    assert {r.id for r in arch_items} == {arch.id}

    # ready_only → only requisites that can take a payin now
    ready_items, _ = await repo.get_by_trader(trader.id, ready_only=True)
    assert {r.id for r in ready_items} == {healthy.id}
    assert off.id not in {r.id for r in ready_items}
