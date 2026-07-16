# backend-py/tests/unit/test_receipt_check_stats_repository.py
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy.dialects import postgresql

from app.modules.stats.repository import ReceiptCheckStatsRepository

DF = datetime(2026, 7, 1, tzinfo=timezone.utc)
DT = datetime(2026, 7, 14, tzinfo=timezone.utc)


def _capture():
    captured = {}

    async def _execute(stmt):
        captured["stmt"] = stmt
        res = MagicMock()
        res.all.return_value = []
        return res

    session = MagicMock()
    session.execute = _execute
    return session, captured


def _pg(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect(),
                            compile_kwargs={"literal_binds": True})).lower()


@pytest.mark.asyncio
async def test_provider_rollup_filters_by_status_verdict_trigger_charge():
    session, captured = _capture()
    await ReceiptCheckStatsRepository(session).provider_rollup(DF, DT)
    sql = _pg(captured["stmt"])
    assert "from receipt_checks" in sql
    assert "left outer join receipt_check_providers" in sql
    # status counts via FILTER
    assert "count(*) filter (where receipt_checks.status = 'success')" in sql
    assert "count(*) filter (where receipt_checks.status = 'failed')" in sql
    # trigger + verdict + charged/refunded aggregates
    assert "filter (where receipt_checks.trigger = 'manual')" in sql
    assert "filter (where receipt_checks.is_clean is false)" in sql
    assert "filter (where receipt_checks.is_clean is not null)" in sql
    assert "sum(receipt_checks.price_usdt) filter (where receipt_checks.charged" in sql
    assert "sum(receipt_checks.price_usdt) filter (where receipt_checks.refunded" in sql
    # date window is present (bounds compared to created_at)
    assert "receipt_checks.created_at >=" in sql and "receipt_checks.created_at <" in sql


@pytest.mark.asyncio
async def test_timeseries_buckets_in_utc_by_granularity():
    session, captured = _capture()
    await ReceiptCheckStatsRepository(session).timeseries(DF, DT, "day")
    sql = _pg(captured["stmt"])
    assert "date_trunc('day', timezone('utc', receipt_checks.created_at))" in sql
    assert "count(*)" in sql and "group by" in sql and "order by" in sql


@pytest.mark.asyncio
async def test_timeseries_3hour_uses_date_bin():
    session, captured = _capture()
    await ReceiptCheckStatsRepository(session).timeseries(DF, DT, "3hour")
    sql = _pg(captured["stmt"])
    # 3hour buckets via date_bin over the UTC-normalized column, 3-hour interval,
    # anchored at the 1970 epoch origin.
    assert (
        "date_bin(interval '3 hours', timezone('utc', receipt_checks.created_at), "
        "timestamp '1970-01-01 00:00:00')"
    ) in sql
    assert "count(*)" in sql and "group by" in sql and "order by" in sql


@pytest.mark.asyncio
async def test_timeseries_rejects_bad_granularity():
    session, _ = _capture()
    with pytest.raises(ValueError):
        await ReceiptCheckStatsRepository(session).timeseries(DF, DT, "minute")


@pytest.mark.asyncio
async def test_top_traders_joins_users_orders_desc_limits():
    session, captured = _capture()
    await ReceiptCheckStatsRepository(session).top_traders(DF, DT, limit=5)
    sql = _pg(captured["stmt"])
    assert "join users" in sql
    assert "receipt_checks.trader_user_id is not null" in sql
    assert "order by" in sql and "desc" in sql and "limit 5" in sql
