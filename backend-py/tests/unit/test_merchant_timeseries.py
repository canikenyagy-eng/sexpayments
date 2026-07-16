"""Unit test for the per-merchant timeseries merge + zero-backfill (no DB).

The repository is mocked so this exercises only the service-side alignment of
the Postgres (created/success) and ClickHouse (requests) buckets and the
continuous zero-filled walk over the range.
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.stats.service import StatsService


@pytest.mark.asyncio
async def test_compute_merchant_timeseries_merges_pg_ch_and_backfills():
    svc = StatsService(MagicMock())
    svc.repository = MagicMock()

    base = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    # PG returns naive-UTC bucket ts (as date_trunc would); CH returns unix.
    svc.repository.get_merchant_order_timeseries = AsyncMock(return_value=[
        {"ts": datetime(2026, 7, 1, 0, 0), "created": 5, "success": 3},
    ])
    svc.repository.payin_requests_timeseries_by_merchant = AsyncMock(return_value=[
        {"bucket": int(base.timestamp()), "requests": 10},
    ])

    res = await svc._compute_merchant_timeseries(
        1, base, base + timedelta(hours=2), "hour",
    )

    assert res["granularity"] == "hour"
    pts = res["points"]
    assert len(pts) == 3  # hourly buckets 00:00, 01:00, 02:00

    # First bucket merges PG (created/success) + CH (requests) on the same ts.
    assert pts[0] == {
        "ts": int(base.timestamp()), "requests": 10, "created": 5, "success": 3,
    }
    # Empty buckets are zero-filled so the chart is continuous.
    assert pts[1]["requests"] == 0 and pts[1]["created"] == 0 and pts[1]["success"] == 0
    assert pts[2]["requests"] == 0

    # Buckets are strictly increasing, one hour apart.
    assert pts[1]["ts"] - pts[0]["ts"] == 3600
    assert pts[2]["ts"] - pts[1]["ts"] == 3600
