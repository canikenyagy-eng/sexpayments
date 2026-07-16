# backend-py/tests/unit/test_receipt_check_stats_service.py
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.stats.repository import (
    CheckerBucketRow, ProviderRollupRow, TraderRollupRow,
)
from app.modules.stats.service import ReceiptCheckStatsService

DF = datetime(2026, 7, 1, tzinfo=timezone.utc)
DT = datetime(2026, 7, 14, tzinfo=timezone.utc)


def _svc(provider_rows=(), trader_rows=(), bucket_rows=()):
    svc = ReceiptCheckStatsService(MagicMock())
    svc.repository = MagicMock()
    svc.repository.provider_rollup = AsyncMock(return_value=list(provider_rows))
    svc.repository.top_traders = AsyncMock(return_value=list(trader_rows))
    svc.repository.timeseries = AsyncMock(return_value=list(bucket_rows))
    return svc


def _prow(**kw):
    base = dict(provider_id=1, name="TREXO", adapter_type="trexo", total=10, success=6,
               failed=2, manual=7, auto=3, clean=5, suspicious=3, decided=8,
               charged_usdt=2.0, refunded_usdt=0.5, charged_count=8)
    base.update(kw)
    return ProviderRollupRow(**base)


@pytest.mark.asyncio
async def test_provider_row_derived_rates_and_net():
    svc = _svc(provider_rows=[_prow()])
    out = await svc._compute_checker_stats(DF, DT)
    p = out["providers"][0]
    assert p["checker"] == "TREXO"
    assert p["suspicious_rate"] == pytest.approx(3 / 8)  # suspicious / decided
    assert p["net_usdt"] == pytest.approx(1.5)        # charged 2.0 - refunded 0.5
    assert p["avg_price_usdt"] == pytest.approx(2.0 / 8)  # charged_usdt / charged_count


@pytest.mark.asyncio
async def test_totals_aggregate_across_providers():
    svc = _svc(provider_rows=[
        _prow(provider_id=1, total=10, success=6, failed=2, charged_usdt=2.0,
              refunded_usdt=0.5, charged_count=8, suspicious=3, decided=8),
        _prow(provider_id=2, name="DETECT", total=5, success=5, failed=0,
              charged_usdt=1.0, refunded_usdt=0.0, charged_count=5, suspicious=1, decided=5),
    ])
    t = (await svc._compute_checker_stats(DF, DT))["totals"]
    assert t["total"] == 15 and t["success"] == 11 and t["failed"] == 2
    assert t["spent_usdt"] == pytest.approx(3.0)
    assert t["refunded_usdt"] == pytest.approx(0.5)  # 0.5+0.0
    assert t["net_usdt"] == pytest.approx(2.5)
    assert t["suspicious_rate"] == pytest.approx(4 / 13)  # (3+1)/(8+5)
    assert t["avg_price_usdt"] == pytest.approx(3.0 / 13)  # (2.0+1.0)/(8+5)


@pytest.mark.asyncio
async def test_null_provider_labeled_unknown():
    svc = _svc(provider_rows=[_prow(provider_id=None, name=None, adapter_type=None)])
    p = (await svc._compute_checker_stats(DF, DT))["providers"][0]
    assert p["provider_id"] is None and p["checker"] == "удалён/неизвестен"


@pytest.mark.asyncio
async def test_empty_is_all_zero_no_div_by_zero():
    t = (await _svc()._compute_checker_stats(DF, DT))["totals"]
    assert t["total"] == 0 and t["suspicious_rate"] == 0.0
    assert t["avg_price_usdt"] == 0.0


@pytest.mark.asyncio
async def test_top_traders_shape_and_rate():
    svc = _svc(trader_rows=[TraderRollupRow(trader_user_id=7, username="ivan", checks=4,
                                            charged_usdt=1.0, suspicious=1, decided=2)])
    tr = (await svc._compute_checker_stats(DF, DT))["top_traders"][0]
    assert tr == {"trader_user_id": 7, "username": "ivan", "checks": 4,
                  "spent_usdt": 1.0, "suspicious_rate": 0.5}


@pytest.mark.asyncio
async def test_stats_default_range_is_about_seven_days():
    # Spec: the 7-day default range applies to the tiles/table too, not just
    # the chart. Both rollup repo calls must receive the resolved window.
    svc = _svc()
    await svc._compute_checker_stats(None, None)

    svc.repository.provider_rollup.assert_awaited_once()
    svc.repository.top_traders.assert_awaited_once()
    for mock in (svc.repository.provider_rollup, svc.repository.top_traders):
        df, dt = mock.await_args.args
        assert df is not None and dt is not None
        assert timedelta(days=6) < dt - df < timedelta(days=8)


# ── _compute_checker_timeseries: auto-granularity ───────────────────────────

@pytest.mark.asyncio
async def test_timeseries_auto_granularity_sub_24h_picks_hour():
    df = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    dt = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    svc = _svc(bucket_rows=[])
    out = await svc._compute_checker_timeseries(df, dt, None)
    assert out["granularity"] == "hour"
    svc.repository.timeseries.assert_awaited_once_with(df, dt, "hour")


@pytest.mark.asyncio
async def test_timeseries_auto_granularity_two_weeks_picks_day():
    df = datetime(2026, 7, 1, tzinfo=timezone.utc)
    dt = df + timedelta(days=14)
    svc = _svc(bucket_rows=[])
    out = await svc._compute_checker_timeseries(df, dt, None)
    assert out["granularity"] == "day"
    svc.repository.timeseries.assert_awaited_once_with(df, dt, "day")


# ── _compute_checker_timeseries: zero-backfill ───────────────────────────────

@pytest.mark.asyncio
async def test_timeseries_zero_backfill_continuous_series():
    df = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    dt = datetime(2026, 7, 1, 3, 0, tzinfo=timezone.utc)
    # One real row landing in the 01:00 bucket (naive UTC, as the repo returns).
    mid_ts = datetime(2026, 7, 1, 1, 0)
    svc = _svc(bucket_rows=[CheckerBucketRow(ts=mid_ts, checks=3, spent_usdt=0.6)])

    out = await svc._compute_checker_timeseries(df, dt, "hour")

    assert out["granularity"] == "hour"
    pts = out["points"]
    # 00:00, 01:00, 02:00, 03:00 inclusive → 4 continuous hourly buckets.
    assert len(pts) == 4
    ts_list = [p["ts"] for p in pts]
    assert all(b - a == 3600 for a, b in zip(ts_list, ts_list[1:]))

    expected_mid_ts = int(datetime(2026, 7, 1, 1, 0, tzinfo=timezone.utc).timestamp())
    by_ts = {p["ts"]: p for p in pts}
    assert by_ts[expected_mid_ts] == {"ts": expected_mid_ts, "checks": 3, "spent_usdt": 0.6}

    # The other three buckets are zero-filled.
    for p in pts:
        if p["ts"] != expected_mid_ts:
            assert p == {"ts": p["ts"], "checks": 0, "spent_usdt": 0.0}


# ── _compute_checker_timeseries: default range ───────────────────────────────

@pytest.mark.asyncio
async def test_timeseries_default_range_is_about_seven_days():
    svc = _svc(bucket_rows=[])
    await svc._compute_checker_timeseries(None, None, "day")

    svc.repository.timeseries.assert_awaited_once()
    called_df, called_dt, called_gran = svc.repository.timeseries.await_args.args
    assert called_df is not None and called_dt is not None
    assert called_gran == "day"
    span = called_dt - called_df
    assert timedelta(days=6) < span < timedelta(days=8)


# ── _compute_checker_timeseries: explicit granularity pass-through ──────────

@pytest.mark.asyncio
async def test_timeseries_explicit_3hour_granularity_passed_through():
    svc = _svc(bucket_rows=[])
    out = await svc._compute_checker_timeseries(DF, DT, "3hour")
    assert out["granularity"] == "3hour"
    svc.repository.timeseries.assert_awaited_once_with(DF, DT, "3hour")


@pytest.mark.asyncio
async def test_timeseries_3hour_bucket_alignment_and_backfill():
    # date_bin path (3hour is manual-only): one real row on a 3h boundary must
    # land in the right output bucket and the surrounding 3h buckets zero-fill.
    # Proves the naive-vs-aware _normalise_repo_ts alignment holds for date_bin,
    # not just date_trunc.
    df = datetime(2026, 7, 1, 0, 0, tzinfo=timezone.utc)
    dt = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    # Repo returns naive-UTC bucket ts, on the 03:00 boundary.
    row_ts = datetime(2026, 7, 1, 3, 0)
    svc = _svc(bucket_rows=[CheckerBucketRow(ts=row_ts, checks=5, spent_usdt=1.25)])

    out = await svc._compute_checker_timeseries(df, dt, "3hour")

    assert out["granularity"] == "3hour"
    pts = out["points"]
    # 00:00, 03:00, 06:00, 09:00 inclusive → 4 continuous 3-hour buckets.
    assert len(pts) == 4
    ts_list = [p["ts"] for p in pts]
    assert all(b - a == 3 * 3600 for a, b in zip(ts_list, ts_list[1:]))

    expected_ts = int(datetime(2026, 7, 1, 3, 0, tzinfo=timezone.utc).timestamp())
    by_ts = {p["ts"]: p for p in pts}
    assert by_ts[expected_ts] == {"ts": expected_ts, "checks": 5, "spent_usdt": 1.25}

    # The other three 3h buckets are zero-filled.
    for p in pts:
        if p["ts"] != expected_ts:
            assert p == {"ts": p["ts"], "checks": 0, "spent_usdt": 0.0}
