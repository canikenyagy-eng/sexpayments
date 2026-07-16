"""
Unit tests for StatsService.

Definition: `payout_pct` ("Выдача %") is the share of created orders relative
to the number of order creation requests received from merchants (external
API + Telegram bot). Formula: `orders_total / payin_requests_total * 100`.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.common.enums.users import UserRole
from app.modules.stats.repository import (
    NewOrdersDelta,
    OrderAggregates,
    RealtimeMetrics,
    UpdatedOrdersDelta,
)
from app.modules.stats.service import StatsService


@pytest.fixture
def mock_session():
    return MagicMock()


@pytest.fixture
def service(mock_session):
    svc = StatsService(mock_session)
    svc.repository = AsyncMock()
    # Cached-stats path adds this delta to profit; default it to a number so the
    # bare AsyncMock doesn't yield a MagicMock into the float arithmetic. Tests
    # that exercise the долив delta override it.
    svc.repository.sum_doliv_margin_since = AsyncMock(return_value=0.0)
    return svc


def _realtime() -> RealtimeMetrics:
    return RealtimeMetrics(
        merchants_online_24h=0,
        traders_online_24h=0,
        pending_withdrawals=0,
        active_disputes=0,
        orders_active=0,
        payin_requests_24h=0,
        requests_rub_24h=0.0,
    )


# ────────────────────────────────────────────────────────────────
# compute_full
# ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compute_full_payout_pct_is_orders_over_requests(service):
    """80 created orders out of 100 merchant deal requests → 80.0 %."""
    service.repository.get_order_aggregates.return_value = OrderAggregates(
        turnover_usdt=0.0,
        profit_usdt=0.0,
        requests_rub=0.0,
        orders_total=80,
        orders_success=60,
        orders_active=5,
        payout_count=10,
    )
    service.repository.get_realtime_metrics.return_value = _realtime()
    service.repository.get_failed_api_requests_rub.return_value = 0.0
    service.repository.count_payin_api_requests.return_value = 100

    stats = await service.compute_full()

    assert stats.payin_requests_total == 100
    assert stats.orders_total == 80
    assert stats.payout_pct == 80.0


@pytest.mark.asyncio
async def test_compute_full_payout_pct_zero_when_no_requests(service):
    """No merchant requests in range → ratio is undefined; we return 0 to keep
    the dashboard division-safe."""
    service.repository.get_order_aggregates.return_value = OrderAggregates(
        turnover_usdt=0.0,
        profit_usdt=0.0,
        requests_rub=0.0,
        orders_total=0,
        orders_success=0,
        orders_active=0,
        payout_count=0,
    )
    service.repository.get_realtime_metrics.return_value = _realtime()
    service.repository.get_failed_api_requests_rub.return_value = 0.0
    service.repository.count_payin_api_requests.return_value = 0

    stats = await service.compute_full()
    assert stats.payout_pct == 0
    assert stats.payin_requests_total == 0


@pytest.mark.asyncio
async def test_compute_full_payout_pct_full_fulfilment(service):
    """Every merchant request became an order: 10/10 → 100%."""
    service.repository.get_order_aggregates.return_value = OrderAggregates(
        turnover_usdt=0.0,
        profit_usdt=0.0,
        requests_rub=0.0,
        orders_total=10,
        orders_success=10,
        orders_active=0,
        payout_count=2,
    )
    service.repository.get_realtime_metrics.return_value = _realtime()
    service.repository.get_failed_api_requests_rub.return_value = 0.0
    service.repository.count_payin_api_requests.return_value = 10

    stats = await service.compute_full()
    assert stats.payout_pct == 100.0


# ────────────────────────────────────────────────────────────────
# get_cached_stats (snapshot + delta path)
# ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_cached_stats_payout_pct_uses_snapshot_plus_delta(service):
    snapshot = MagicMock()
    snapshot.turnover_usdt = 0
    snapshot.profit_usdt = 0
    snapshot.requests_rub = 0
    snapshot.orders_total = 80
    snapshot.orders_success = 70
    snapshot.payout_count = 20
    snapshot.payin_requests_total = 100
    snapshot.data_cutoff_at = datetime(2026, 1, 1)

    service.repository.get_latest_snapshot.return_value = snapshot
    service.repository.get_new_orders_delta.return_value = NewOrdersDelta(
        total=20, success=10, payouts=10, turnover=0.0, profit=0.0, requests=0.0
    )
    service.repository.get_updated_orders_delta.return_value = UpdatedOrdersDelta(
        new_successes=0, turnover=0.0, profit=0.0
    )
    service.repository.get_realtime_metrics.return_value = _realtime()
    service.repository.get_failed_api_requests_rub_since.return_value = 0.0
    service.repository.count_payin_api_requests_since.return_value = 25

    stats = await service.get_cached_stats()

    # total orders = 80 + 20 = 100; total requests = 100 + 25 = 125
    # payout_pct = 100 / 125 * 100 = 80.0
    assert stats.orders_total == 100
    assert stats.payin_requests_total == 125
    assert stats.payout_pct == 80.0


@pytest.mark.asyncio
async def test_get_cached_stats_payout_pct_zero_when_no_requests(service):
    snapshot = MagicMock()
    snapshot.turnover_usdt = 0
    snapshot.profit_usdt = 0
    snapshot.requests_rub = 0
    snapshot.orders_total = 0
    snapshot.orders_success = 0
    snapshot.payout_count = 0
    snapshot.payin_requests_total = 0
    snapshot.data_cutoff_at = datetime(2026, 1, 1)

    service.repository.get_latest_snapshot.return_value = snapshot
    service.repository.get_new_orders_delta.return_value = NewOrdersDelta(
        total=0, success=0, payouts=0, turnover=0.0, profit=0.0, requests=0.0
    )
    service.repository.get_updated_orders_delta.return_value = UpdatedOrdersDelta(
        new_successes=0, turnover=0.0, profit=0.0
    )
    service.repository.get_realtime_metrics.return_value = _realtime()
    service.repository.get_failed_api_requests_rub_since.return_value = 0.0
    service.repository.count_payin_api_requests_since.return_value = 0

    stats = await service.get_cached_stats()
    assert stats.payout_pct == 0
    assert stats.payin_requests_total == 0


@pytest.mark.asyncio
async def test_get_cached_stats_handles_legacy_snapshot_without_requests_field(service):
    """Snapshots written before migration 019 have no `payin_requests_total`."""
    legacy_snapshot = MagicMock()
    legacy_snapshot.turnover_usdt = 0
    legacy_snapshot.profit_usdt = 0
    legacy_snapshot.requests_rub = 0
    legacy_snapshot.orders_total = 50
    legacy_snapshot.orders_success = 40
    legacy_snapshot.payout_count = 10
    # Simulate ORM attribute missing (e.g. None when default applied later).
    legacy_snapshot.payin_requests_total = None
    legacy_snapshot.data_cutoff_at = datetime(2026, 1, 1)

    service.repository.get_latest_snapshot.return_value = legacy_snapshot
    service.repository.get_new_orders_delta.return_value = NewOrdersDelta(
        total=0, success=0, payouts=0, turnover=0.0, profit=0.0, requests=0.0
    )
    service.repository.get_updated_orders_delta.return_value = UpdatedOrdersDelta(
        new_successes=0, turnover=0.0, profit=0.0
    )
    service.repository.get_realtime_metrics.return_value = _realtime()
    service.repository.get_failed_api_requests_rub_since.return_value = 0.0
    service.repository.count_payin_api_requests_since.return_value = 100

    stats = await service.get_cached_stats()

    # Legacy snapshot contributes 0 to requests, only delta counts.
    # orders_total stays 50, requests = 100 → 50/100 = 50%
    assert stats.payin_requests_total == 100
    assert stats.payout_pct == 50.0


@pytest.mark.asyncio
async def test_get_cached_stats_adds_doliv_margin_delta_to_profit(service):
    """доливы COMPLETED after the snapshot cutoff add their margin (price −
    reward) to the cached dashboard profit, on top of snapshot + order deltas."""
    snapshot = MagicMock()
    snapshot.turnover_usdt = 0
    snapshot.profit_usdt = 100.0  # baked-in order + долив profit as of cutoff
    snapshot.requests_rub = 0
    snapshot.orders_total = 0
    snapshot.orders_success = 0
    snapshot.payout_count = 0
    snapshot.payin_requests_total = 0
    snapshot.data_cutoff_at = datetime(2026, 1, 1)

    service.repository.get_latest_snapshot.return_value = snapshot
    service.repository.get_new_orders_delta.return_value = NewOrdersDelta(
        total=0, success=0, payouts=0, turnover=0.0, profit=5.0, requests=0.0
    )
    service.repository.get_updated_orders_delta.return_value = UpdatedOrdersDelta(
        new_successes=0, turnover=0.0, profit=3.0
    )
    service.repository.get_realtime_metrics.return_value = _realtime()
    service.repository.count_payin_api_requests_since.return_value = 0
    service.repository.sum_payin_api_requests_rub_since.return_value = 0.0
    # долив margin realized since the snapshot.
    service.repository.sum_doliv_margin_since.return_value = 11.0

    stats = await service.get_cached_stats()

    # 100 (snapshot) + 5 (new) + 3 (upd) + 11 (долив) = 119
    assert stats.profit_usdt == pytest.approx(119.0)
    service.repository.sum_doliv_margin_since.assert_awaited_once_with(
        snapshot.data_cutoff_at
    )


# ────────────────────────────────────────────────────────────────
# get_active_stats — pending_withdrawals coverage for every role
# (regression for the bug where trader/teamlead always returned 0)
# ────────────────────────────────────────────────────────────────


def _user(role: UserRole, user_id: int = 1) -> MagicMock:
    user = MagicMock()
    user.id = user_id
    user.role = role
    return user


@pytest.mark.asyncio
async def test_active_stats_trader_uses_real_pending_withdrawals(service):
    service.repository.count_active_orders_for_trader = AsyncMock(return_value=2)
    service.repository.count_active_disputes_for_trader = AsyncMock(return_value=1)
    service.repository.count_pending_withdrawals_for_user = AsyncMock(return_value=3)
    service.repository.count_traffic_accepting_requisites_for_trader = AsyncMock(
        return_value=4
    )

    stats = await service.get_active_stats(_user(UserRole.TRADER, user_id=42))

    assert stats.orders_active == 2
    assert stats.active_disputes == 1
    assert stats.pending_withdrawals == 3
    assert stats.requisites_traffic_active == 4
    service.repository.count_pending_withdrawals_for_user.assert_awaited_once_with(
        42, UserRole.TRADER
    )
    service.repository.count_traffic_accepting_requisites_for_trader.assert_awaited_once_with(
        42
    )


@pytest.mark.asyncio
async def test_active_stats_teamlead_uses_real_pending_withdrawals(service):
    service.repository.count_pending_withdrawals_for_user = AsyncMock(return_value=5)

    stats = await service.get_active_stats(_user(UserRole.TEAMLEAD, user_id=77))

    assert stats.pending_withdrawals == 5
    # Teamleads have no orders/disputes/requisites scoped to themselves.
    assert stats.orders_active == 0
    assert stats.active_disputes == 0
    assert stats.requisites_traffic_active == 0
    service.repository.count_pending_withdrawals_for_user.assert_awaited_once_with(
        77, UserRole.TEAMLEAD
    )


@pytest.mark.asyncio
async def test_active_stats_merchant_still_uses_dedicated_repo_method(service):
    """Sanity check that merchant path is not regressed by the new branch."""
    service.repository.count_active_orders_for_merchant_user = AsyncMock(return_value=4)
    service.repository.count_active_disputes_for_merchant_user = AsyncMock(return_value=2)
    service.repository.count_pending_withdrawals_for_merchant_user = AsyncMock(
        return_value=7
    )
    # The trader/teamlead helper must NOT be called for a merchant.
    service.repository.count_pending_withdrawals_for_user = AsyncMock()

    stats = await service.get_active_stats(_user(UserRole.MERCHANT, user_id=11))

    assert stats.pending_withdrawals == 7
    service.repository.count_pending_withdrawals_for_merchant_user.assert_awaited_once_with(11)
    service.repository.count_pending_withdrawals_for_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_active_stats_admin_includes_traffic_accepting_requisites(service):
    """Admin badge: global count of requisites ready to accept traffic."""
    service.repository.count_active_orders_global = AsyncMock(return_value=5)
    service.repository.count_active_disputes_global = AsyncMock(return_value=2)
    service.repository.count_pending_withdrawals_global = AsyncMock(return_value=1)
    service.repository.count_traffic_accepting_requisites_global = AsyncMock(return_value=9)

    stats = await service.get_active_stats(_user(UserRole.ADMIN, user_id=1))

    assert stats.orders_active == 5
    assert stats.active_disputes == 2
    assert stats.pending_withdrawals == 1
    assert stats.requisites_traffic_active == 9
    service.repository.count_traffic_accepting_requisites_global.assert_awaited_once_with()


# ────────────────────────────────────────────────────────────────
# StatsRepository.count_pending_withdrawals_for_merchant_user
# regression: the WHERE clause must not contain an unguarded
# `user_id == :user_id` comparison, otherwise legacy rows whose
# `user_id` (= other merchant's terminal id) collide with the
# current user's `users.id`.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_pending_withdrawals_merchant_user_sql_has_no_unguarded_user_id_branch():
    from sqlalchemy.dialects import postgresql

    # Import model modules with relationships referenced from other mappers
    # so SQLAlchemy can resolve string-based `relationship("Requisite")` etc.
    # at compile time. Without these imports, transitively-pulled mappers
    # (Order, Merchant, …) fail to locate referenced classes when we call
    # statement.compile().
    from app.modules.payments.models import PaymentOption  # noqa: F401
    from app.modules.requisites.models import Requisite  # noqa: F401
    from app.modules.stats.repository import StatsRepository

    captured = {}

    async def _capture_execute(stmt):
        captured["stmt"] = stmt
        result = MagicMock()
        result.scalar_one.return_value = 0
        return result

    session = MagicMock()
    session.execute = _capture_execute
    repo = StatsRepository(session)

    await repo.count_pending_withdrawals_for_merchant_user(user_id=42)

    compiled = captured["stmt"].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    sql = str(compiled).lower()

    # Branch 1 (matched terminal) must remain.
    assert "withdrawal_requests.merchant_id in" in sql
    # Branch 3 (legacy) must remain — and be guarded by IS NULL.
    assert "withdrawal_requests.merchant_id is null" in sql
    assert "withdrawal_requests.user_id in" in sql

    # Branch 2 in its unsafe form must be gone:
    # a bare `withdrawal_requests.user_id = 42` would match strangers' legacy
    # rows whose merchants.id happens to equal this users.id.
    assert "withdrawal_requests.user_id = 42" not in sql


# ────────────────────────────────────────────────────────────────
# StatsRepository.count_traffic_accepting_requisites_for_trader
# Sidebar badge for traders. Must filter by:
#   - requisite.trader_id == :user_id
#   - requisite.status == 'enabled'
#   - requisite.is_archived == false
#   - trader.status == 'enabled'
#   - trader.is_payin_active == true
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_traffic_accepting_requisites_for_trader_sql_shape():
    from sqlalchemy.dialects import postgresql

    from app.modules.payments.models import PaymentOption  # noqa: F401
    from app.modules.requisites.models import Requisite  # noqa: F401
    from app.modules.traders.models import Trader  # noqa: F401
    from app.modules.stats.repository import StatsRepository

    captured = {}

    async def _capture_execute(stmt):
        captured["stmt"] = stmt
        result = MagicMock()
        result.scalar_one.return_value = 3
        return result

    session = MagicMock()
    session.execute = _capture_execute
    repo = StatsRepository(session)

    count = await repo.count_traffic_accepting_requisites_for_trader(99)
    assert count == 3

    compiled = captured["stmt"].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    sql = str(compiled).lower()

    assert "from requisites" in sql
    assert "join traders" in sql
    assert "requisites.trader_id = 99" in sql
    assert "requisites.status = 'enabled'" in sql
    assert "requisites.is_archived is false" in sql
    assert "traders.status = 'enabled'" in sql
    assert "traders.is_payin_active is true" in sql
    # The fix: trader badge is now capacity-aware (was missing these).
    assert "requisites.is_active is true" in sql
    assert "requisites.source" in sql
    assert "join requisite_limits" in sql
    assert "limit_max_concurrent_orders" in sql
    assert "current_daily_turnover" in sql


@pytest.mark.asyncio
async def test_count_traffic_accepting_requisites_global_sql_shape():
    """Admin badge: global «can take a payin RIGHT NOW» gate. Now shares the
    capacity-aware ``ready_requisite_ids_select`` predicate — static gates
    (is_active + LOCAL + ENABLED, trader enabled/payin, positive WORK/USDT
    balance) PLUS capacity (concurrency slot + daily/monthly headroom)."""
    from sqlalchemy.dialects import postgresql

    from app.modules.payments.models import PaymentOption  # noqa: F401
    from app.modules.requisites.models import Requisite  # noqa: F401
    from app.modules.traders.models import Trader  # noqa: F401
    from app.modules.finance.models import Balance  # noqa: F401
    from app.modules.stats.repository import StatsRepository

    captured = {}

    async def _capture_execute(stmt):
        captured["stmt"] = stmt
        result = MagicMock()
        result.scalar_one.return_value = 7
        return result

    session = MagicMock()
    session.execute = _capture_execute
    repo = StatsRepository(session)

    count = await repo.count_traffic_accepting_requisites_global()
    assert count == 7

    compiled = captured["stmt"].compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    sql = str(compiled).lower()

    # Static gates.
    assert "from requisites" in sql
    assert "join traders" in sql
    assert "join balances" in sql
    assert "requisites.is_active is true" in sql
    assert "requisites.is_archived is false" in sql
    assert "requisites.status = 'enabled'" in sql
    assert "requisites.source" in sql
    assert "traders.status = 'enabled'" in sql
    assert "traders.is_payin_active is true" in sql
    assert "balances.amount" in sql and "> 0" in sql
    # Capacity gates (the fix): limits joined + concurrency + daily/monthly room.
    assert "join requisite_limits" in sql
    assert "limit_max_concurrent_orders" in sql
    assert "current_daily_turnover" in sql
    assert "current_monthly_turnover" in sql


# ────────────────────────────────────────────────────────────────
# refresh_snapshot — full recompute then upsert.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_snapshot_persists_compute_full_result(service):
    """Celery `_refresh_snapshot_async` calls this. The service must
    recompute live and hand the entire blob to the repo, including a
    `data_cutoff_at` timestamp."""
    service.repository.get_order_aggregates.return_value = OrderAggregates(
        turnover_usdt=12.5,
        profit_usdt=1.5,
        requests_rub=999.0,
        orders_total=10,
        orders_success=8,
        orders_active=2,
        payout_count=3,
    )
    service.repository.get_realtime_metrics.return_value = RealtimeMetrics(
        merchants_online_24h=1,
        traders_online_24h=2,
        pending_withdrawals=3,
        active_disputes=4,
        orders_active=5,
        payin_requests_24h=6,
        requests_rub_24h=7.0,
    )
    service.repository.get_failed_api_requests_rub.return_value = 0.0
    service.repository.count_payin_api_requests.return_value = 50
    service.repository.upsert_snapshot = AsyncMock()

    await service.refresh_snapshot()

    service.repository.upsert_snapshot.assert_awaited_once()
    blob = service.repository.upsert_snapshot.call_args[0][0]
    assert blob["turnover_usdt"] == 12.5
    assert blob["orders_total"] == 10
    # 10 orders / 50 requests = 20% payout
    assert blob["payout_pct"] == 20.0
    assert blob["payin_requests_total"] == 50
    assert blob["data_cutoff_at"] is not None


# ────────────────────────────────────────────────────────────────
# compute_merchant_full / get_cached_merchant_stats — merchant
# dashboard data path.
# ────────────────────────────────────────────────────────────────


def _merchant_aggregates(**overrides):
    from app.modules.stats.repository import MerchantOrderAggregates
    base = dict(
        turnover_usdt=100.0, fee_usdt=2.5,
        orders_total=4, orders_success=3,
        orders_active=1, orders_failed=0,
    )
    base.update(overrides)
    return MerchantOrderAggregates(**base)


def _merchant_realtime(**overrides):
    from app.modules.stats.repository import MerchantRealtimeMetrics
    base = dict(pending_withdrawals=0, active_disputes=0, orders_active=1)
    base.update(overrides)
    return MerchantRealtimeMetrics(**base)


@pytest.mark.asyncio
async def test_compute_merchant_full_basic_aggregation(service):
    service.repository.get_merchant_order_aggregates = AsyncMock(
        return_value=_merchant_aggregates()
    )
    service.repository.get_merchant_realtime_metrics = AsyncMock(
        return_value=_merchant_realtime(pending_withdrawals=2, active_disputes=1)
    )
    # «Запросов RUB» / «Выдача %» — merchant-scoped API-request метрики.
    service.repository.count_payin_api_requests = AsyncMock(return_value=8)
    service.repository.sum_payin_api_requests_rub = AsyncMock(return_value=120000.0)

    stats = await service.compute_merchant_full(7)

    assert stats.turnover_usdt == 100.0
    assert stats.orders_total == 4
    assert stats.orders_success == 3
    # 3 / 4 * 100 = 75
    assert stats.conversion_pct == 75.0
    assert stats.pending_withdrawals == 2
    assert stats.active_disputes == 1
    # requests_rub проброшен из API-логов
    assert stats.requests_rub == 120000.0
    assert stats.payin_requests_total == 8
    # «Выдача %» = orders_total / payin_requests_total = 4 / 8 * 100 = 50
    assert stats.payout_pct == 50.0


@pytest.mark.asyncio
async def test_compute_merchant_full_payout_pct_zero_requests(service):
    """0 payin API-запросов → payout_pct=0 без деления на ноль."""
    service.repository.get_merchant_order_aggregates = AsyncMock(
        return_value=_merchant_aggregates(orders_total=0, orders_success=0)
    )
    service.repository.get_merchant_realtime_metrics = AsyncMock(
        return_value=_merchant_realtime()
    )
    service.repository.count_payin_api_requests = AsyncMock(return_value=0)
    service.repository.sum_payin_api_requests_rub = AsyncMock(return_value=0.0)

    stats = await service.compute_merchant_full(7)

    assert stats.payout_pct == 0
    assert stats.requests_rub == 0.0
    assert stats.payin_requests_total == 0


@pytest.mark.asyncio
async def test_compute_merchant_full_zero_orders_safe_division(service):
    service.repository.get_merchant_order_aggregates = AsyncMock(
        return_value=_merchant_aggregates(
            turnover_usdt=0, fee_usdt=0,
            orders_total=0, orders_success=0,
            orders_active=0, orders_failed=0,
        )
    )
    service.repository.get_merchant_realtime_metrics = AsyncMock(
        return_value=_merchant_realtime()
    )
    service.repository.count_payin_api_requests = AsyncMock(return_value=0)
    service.repository.sum_payin_api_requests_rub = AsyncMock(return_value=0.0)

    stats = await service.compute_merchant_full(7)

    assert stats.conversion_pct == 0
    assert stats.payout_pct == 0


@pytest.mark.asyncio
async def test_get_cached_merchant_stats_no_snapshot_falls_through_to_full(service):
    service.repository.get_merchant_snapshot = AsyncMock(return_value=None)
    service.repository.get_merchant_order_aggregates = AsyncMock(
        return_value=_merchant_aggregates(orders_total=2, orders_success=1)
    )
    service.repository.get_merchant_realtime_metrics = AsyncMock(
        return_value=_merchant_realtime()
    )

    stats = await service.get_cached_merchant_stats(7)

    # When no snapshot exists, compute_merchant_full numbers are returned as-is.
    assert stats.orders_total == 2
    assert stats.orders_success == 1


@pytest.mark.asyncio
async def test_get_cached_merchant_stats_combines_snapshot_and_delta(service):
    from app.modules.stats.repository import (
        MerchantNewOrdersDelta,
        MerchantUpdatedOrdersDelta,
    )

    snapshot = MagicMock()
    snapshot.turnover_usdt = 10.0
    snapshot.fee_usdt = 1.0
    snapshot.orders_total = 5
    snapshot.orders_success = 4
    snapshot.orders_failed = 1
    snapshot.requests_rub = 50000.0
    snapshot.payin_requests_total = 6
    snapshot.data_cutoff_at = datetime(2026, 1, 1)

    service.repository.get_merchant_snapshot = AsyncMock(return_value=snapshot)
    service.repository.get_merchant_new_orders_delta = AsyncMock(
        return_value=MerchantNewOrdersDelta(
            total=3, success=2, failed=1, turnover=5.0, fee=0.5,
        )
    )
    service.repository.get_merchant_updated_orders_delta = AsyncMock(
        return_value=MerchantUpdatedOrdersDelta(
            new_successes=1, turnover=2.0, fee=0.2,
        )
    )
    service.repository.get_merchant_realtime_metrics = AsyncMock(
        return_value=_merchant_realtime(orders_active=4)
    )
    # «Запросов RUB» / «Выдача %» delta since cutoff.
    service.repository.sum_payin_api_requests_rub_since = AsyncMock(return_value=10000.0)
    service.repository.count_payin_api_requests_since = AsyncMock(return_value=4)

    stats = await service.get_cached_merchant_stats(7)

    # totals = snapshot + new
    assert stats.orders_total == 5 + 3
    # success = snapshot + new + updated_to_success
    assert stats.orders_success == 4 + 2 + 1
    assert stats.orders_failed == 5 + 1 - 4  # 1 + 1 = 2  (snapshot.orders_failed + new.failed)
    # turnover = snapshot + new + updated
    assert stats.turnover_usdt == 10.0 + 5.0 + 2.0
    assert stats.fee_usdt == 1.0 + 0.5 + 0.2
    # orders_active comes from realtime metrics
    assert stats.orders_active == 4
    # requests_rub = snapshot + delta since cutoff
    assert stats.requests_rub == 50000.0 + 10000.0
    assert stats.payin_requests_total == 6 + 4
    # «Выдача %» = orders_total(8) / payin_requests_total(10) * 100 = 80
    assert stats.payout_pct == 80.0


@pytest.mark.asyncio
async def test_get_cached_merchant_stats_with_date_filters_runs_full_compute(service):
    from datetime import datetime as _dt

    service.repository.get_merchant_snapshot = AsyncMock()
    service.repository.get_merchant_order_aggregates = AsyncMock(
        return_value=_merchant_aggregates(orders_total=9)
    )
    service.repository.get_merchant_realtime_metrics = AsyncMock(
        return_value=_merchant_realtime()
    )

    stats = await service.get_cached_merchant_stats(
        7, date_from=_dt(2026, 1, 1), date_to=_dt(2026, 1, 31),
    )

    assert stats.orders_total == 9
    # Snapshot path must be skipped entirely when date filters are present.
    service.repository.get_merchant_snapshot.assert_not_called()


# ────────────────────────────────────────────────────────────────
# refresh_merchant_snapshots — iterate over every merchant.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_merchant_snapshots_writes_one_per_merchant(service):
    service.repository.get_all_merchant_ids = AsyncMock(return_value=[1, 2])
    service.repository.get_merchant_order_aggregates = AsyncMock(
        return_value=_merchant_aggregates()
    )
    service.repository.get_merchant_realtime_metrics = AsyncMock(
        return_value=_merchant_realtime()
    )
    service.repository.upsert_merchant_snapshot = AsyncMock()

    await service.refresh_merchant_snapshots()

    assert service.repository.upsert_merchant_snapshot.await_count == 2
    seen_ids = [c.args[0] for c in service.repository.upsert_merchant_snapshot.await_args_list]
    assert seen_ids == [1, 2]
    # Every blob includes data_cutoff_at
    for call in service.repository.upsert_merchant_snapshot.await_args_list:
        assert "data_cutoff_at" in call.args[1]


# ────────────────────────────────────────────────────────────────
# list_order_creation_requests — JSON parsing edge cases.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_order_creation_requests_basic(service):
    row = MagicMock()
    row.id = 1
    row.merchant_id = 5
    row.merchant_login = "m1"
    row.request_data = {"amount": "1500.5", "method": "card"}
    row.result = {"success": True}
    row.response_time_ms = 42
    row.created_at = datetime(2026, 5, 1)

    service.repository.list_order_creation_requests = AsyncMock(return_value=([row], 1))

    out = await service.list_order_creation_requests(skip=0, limit=10)

    assert out["total"] == 1
    [item] = out["items"]
    assert item["amount_rub"] == 1500.5
    assert item["method"] == "card"
    assert item["success"] is True
    assert item["response_time_ms"] == 42


@pytest.mark.asyncio
async def test_list_order_creation_requests_handles_corrupt_amount(service):
    """An amount that can't be parsed → `amount_rub` becomes None instead of raising."""
    row = MagicMock()
    row.id = 1
    row.merchant_id = 5
    row.merchant_login = "m1"
    row.request_data = {"amount": "not-a-number", "payment_method": "sbp"}
    row.result = {}
    row.response_time_ms = None
    row.created_at = datetime(2026, 5, 1)

    service.repository.list_order_creation_requests = AsyncMock(return_value=([row], 1))

    out = await service.list_order_creation_requests()

    item = out["items"][0]
    assert item["amount_rub"] is None
    assert item["method"] == "sbp"
    assert item["success"] is False  # missing -> False
    assert item["response_time_ms"] is None


@pytest.mark.asyncio
async def test_list_order_creation_requests_empty_payload(service):
    row = MagicMock()
    row.id = 2
    row.merchant_id = 5
    row.merchant_login = "m1"
    row.request_data = None
    row.result = None
    row.response_time_ms = 5
    row.created_at = datetime(2026, 5, 1)

    service.repository.list_order_creation_requests = AsyncMock(return_value=([row], 1))

    out = await service.list_order_creation_requests()

    item = out["items"][0]
    assert item["amount_rub"] is None
    assert item["method"] is None
    assert item["success"] is False


# ────────────────────────────────────────────────────────────────
# get_volume_distribution_24h — pure pass-through.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_volume_distribution_24h_passes_through(service):
    payload = [{"bank": "sber", "rub": 100.0}]
    service.repository.get_volume_distribution_24h = AsyncMock(return_value=payload)

    result = await service.get_volume_distribution_24h()

    assert result is payload


# ────────────────────────────────────────────────────────────────
# get_admin_timeseries — granularity auto-pick + zero gap fill.
# ────────────────────────────────────────────────────────────────


def _ts_point(ts: datetime, *, turnover=10.0, profit=2.0, orders=1):
    from app.modules.stats.repository import TimeseriesPoint
    return TimeseriesPoint(ts=ts, turnover_usdt=turnover, profit_usdt=profit, orders=orders)


@pytest.mark.asyncio
async def test_get_admin_timeseries_granularity_auto_hour_for_24h(service):
    from datetime import timezone

    service.repository.get_admin_timeseries = AsyncMock(return_value=[])
    end = datetime(2026, 5, 8, 12, 0, tzinfo=timezone.utc)
    start = datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc)

    result = await service.get_admin_timeseries(start, end)

    assert result["granularity"] == "hour"
    # 13 hourly buckets: 00..12 inclusive
    assert len(result["points"]) == 13
    # All zeros because repo returned empty
    assert all(p["turnover_usdt"] == 0.0 and p["orders"] == 0 for p in result["points"])


@pytest.mark.asyncio
async def test_get_admin_timeseries_explicit_3hour_buckets(service):
    """Granularity '3hour' buckets the series into 3-hour intervals (one point =
    the sum over three hours), aligned to 00:00/03:00/… and back-filled."""
    from datetime import timezone

    service.repository.get_admin_timeseries = AsyncMock(return_value=[])
    start = datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 5, 8, 6, 0, tzinfo=timezone.utc)

    result = await service.get_admin_timeseries(start, end, granularity="3hour")

    assert result["granularity"] == "3hour"
    assert service.repository.get_admin_timeseries.await_args.args[2] == "3hour"
    assert len(result["points"]) == 3
    assert result["points"][1]["ts"] - result["points"][0]["ts"] == 3 * 3600
    assert result["points"][2]["ts"] - result["points"][1]["ts"] == 3 * 3600


@pytest.mark.asyncio
async def test_get_admin_timeseries_granularity_auto_day_for_week(service):
    from datetime import timezone

    service.repository.get_admin_timeseries = AsyncMock(return_value=[])
    end = datetime(2026, 5, 8, tzinfo=timezone.utc)
    start = end - timedelta(days=7)

    result = await service.get_admin_timeseries(start, end)

    assert result["granularity"] == "day"
    assert len(result["points"]) == 8  # 7 days inclusive of both ends


@pytest.mark.asyncio
async def test_get_admin_timeseries_granularity_auto_week_for_long(service):
    from datetime import timezone

    service.repository.get_admin_timeseries = AsyncMock(return_value=[])
    end = datetime(2026, 5, 8, tzinfo=timezone.utc)
    start = end - timedelta(days=60)

    result = await service.get_admin_timeseries(start, end)

    assert result["granularity"] == "week"


@pytest.mark.asyncio
async def test_get_admin_timeseries_granularity_auto_month_for_year(service):
    from datetime import timezone

    service.repository.get_admin_timeseries = AsyncMock(return_value=[])
    end = datetime(2026, 5, 8, tzinfo=timezone.utc)
    start = end - timedelta(days=365)

    result = await service.get_admin_timeseries(start, end)

    assert result["granularity"] == "month"


@pytest.mark.asyncio
async def test_get_admin_timeseries_fills_gaps_and_keeps_repo_data(service):
    """Empty buckets between repo points must appear as zero points so the
    chart renders a continuous line."""
    from datetime import timezone

    end = datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc)
    start = datetime(2026, 5, 5, 0, 0, tzinfo=timezone.utc)

    # Repo only has one point on day=2026-05-06 with non-zero values
    repo_point = _ts_point(datetime(2026, 5, 6), turnover=42.0, profit=4.0, orders=3)
    service.repository.get_admin_timeseries = AsyncMock(return_value=[repo_point])

    result = await service.get_admin_timeseries(start, end, granularity="day")

    assert result["granularity"] == "day"
    points = result["points"]
    assert len(points) == 4  # 5,6,7,8
    # The bucket matching repo_point should keep its values.
    matched = [p for p in points if p["turnover_usdt"] == 42.0]
    assert len(matched) == 1
    assert matched[0]["orders"] == 3
    # Three other buckets are zero gap-fills.
    zero_points = [p for p in points if p["turnover_usdt"] == 0.0]
    assert len(zero_points) == 3


@pytest.mark.asyncio
async def test_get_admin_timeseries_default_range_last_7_days(service):
    """Both date_from and date_to are None → service backfills "last 7 days"."""
    service.repository.get_admin_timeseries = AsyncMock(return_value=[])

    result = await service.get_admin_timeseries(None, None)

    # Default range is 7 days → granularity='day' (≤ 31d) and 8 buckets inclusive.
    assert result["granularity"] == "day"
    assert len(result["points"]) == 8


@pytest.mark.asyncio
async def test_get_admin_timeseries_normalises_naive_datetimes(service):
    """Caller may pass naive datetime — service must not raise TypeError."""
    service.repository.get_admin_timeseries = AsyncMock(return_value=[])

    naive_start = datetime(2026, 5, 5)
    naive_end = datetime(2026, 5, 8)
    result = await service.get_admin_timeseries(naive_start, naive_end)

    assert result["granularity"] == "day"
    assert len(result["points"]) == 4


# ────────────────────────────────────────────────────────────────
# StatsRepository.get_order_aggregates
# regression: COMPLETED доливы are Payout rows (not Orders), so their
# margin (price − reward) is folded into profit_usdt on top of the
# order-based gross profit, minus teamlead rewards.
# ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_order_aggregates_folds_doliv_margin_into_profit():
    from app.modules.stats.repository import StatsRepository

    # Every inline scalar query (turnover, gross order profit, requests_rub,
    # the counts) returns this; the two summed sub-queries are stubbed below.
    GROSS = 100.0

    async def _execute(stmt):
        result = MagicMock()
        result.scalar_one.return_value = GROSS
        return result

    session = MagicMock()
    session.execute = _execute
    repo = StatsRepository(session)
    # Teamlead rewards are SUBTRACTED, долив margin is ADDED.
    repo._sum_teamlead_rewards_for_success = AsyncMock(return_value=7.0)
    repo._doliv_margin_usdt = AsyncMock(return_value=11.0)

    agg = await repo.get_order_aggregates()

    # gross 100 − teamlead 7 + долив 11 = 104 (would be 93 without the долив term).
    assert agg.profit_usdt == pytest.approx(104.0)
    repo._doliv_margin_usdt.assert_awaited_once()
