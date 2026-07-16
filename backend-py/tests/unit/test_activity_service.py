"""Unit tests for the requisite-activity feature («Активность» tab).

Covers the pure geometry helpers (`_merge_intervals`, `_segments`), the read-side
bucketing/backfill (`_compute_activity_timeseries`), and the snapshot writer
(`snapshot_requisite_activity`). ClickHouse is monkeypatched — these are pure
logic tests, no CH/DB.
"""
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.common.enums.finances import Currency
from app.modules.stats import activity_ch
from app.modules.stats.service import StatsService, _merge_intervals, _segments


@pytest.fixture
def service():
    svc = StatsService(MagicMock())
    svc.repository = AsyncMock()
    return svc


def _unix(y, m, d, hh=0):
    return int(datetime(y, m, d, hh, 0, tzinfo=timezone.utc).timestamp())


# ── _merge_intervals ─────────────────────────────────────────────────────────

def test_merge_intervals_spec_example():
    # The exact example from the feature spec.
    merged = _merge_intervals([(100, 1000), (500, 2000), (5000, 5500)])
    assert merged == [(100.0, 2000.0), (5000.0, 5500.0)]


def test_merge_intervals_touching_ranges_merge():
    assert _merge_intervals([(100, 1000), (1000, 2000)]) == [(100.0, 2000.0)]


def test_merge_intervals_disjoint_kept_separate():
    assert _merge_intervals([(100, 200), (500, 600)]) == [(100.0, 200.0), (500.0, 600.0)]


def test_merge_intervals_unordered_input():
    assert _merge_intervals([(5000, 5500), (100, 1000), (500, 2000)]) == [
        (100.0, 2000.0), (5000.0, 5500.0),
    ]


def test_merge_intervals_empty():
    assert _merge_intervals([]) == []


# ── _segments (coverage step-function) ───────────────────────────────────────

def test_segments_overlap_produces_depth_steps():
    # (100,1000) and (500,2000) merge to bar (100,2000); coverage:
    #   [100,500)=1, [500,1000)=2, [1000,2000)=1.
    segs = _segments(100.0, 2000.0, [(100, 1000), (500, 2000)])
    assert segs == [
        {"from_amount": 100.0, "to_amount": 500.0, "requisites": 1},
        {"from_amount": 500.0, "to_amount": 1000.0, "requisites": 2},
        {"from_amount": 1000.0, "to_amount": 2000.0, "requisites": 1},
    ]


def test_segments_single_interval_is_one_slice():
    assert _segments(5000.0, 5500.0, [(5000, 5500)]) == [
        {"from_amount": 5000.0, "to_amount": 5500.0, "requisites": 1},
    ]


def test_segments_merges_adjacent_equal_counts():
    # Three identical ranges → one slice of depth 3 (not three slices).
    segs = _segments(0.0, 100.0, [(0, 100), (0, 100), (0, 100)])
    assert segs == [{"from_amount": 0.0, "to_amount": 100.0, "requisites": 3}]


def test_segments_excludes_fixed_amount_point_from_band():
    # A fixed-amount requisite (min == max) is measure-zero: it must NOT inflate
    # the covering count across the whole band (which a hover almost always hits).
    segs = _segments(1000.0, 5000.0, [(1000, 5000), (1000, 1000)])
    assert segs == [{"from_amount": 1000.0, "to_amount": 5000.0, "requisites": 1}]


def test_segments_pure_point_bar_has_no_bands():
    # A bucket of only fixed-amount requisites has no positive-width coverage.
    assert _segments(1000.0, 1000.0, [(1000, 1000), (1000, 1000)]) == []


# ── _compute_activity_timeseries ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_compute_activity_buckets_bars_and_trader_count(service, monkeypatch):
    b0 = _unix(2026, 5, 8, 0)
    rows = [
        {"bucket": b0, "requisite_id": 1, "trader_id": 10, "mn": Decimal("100"), "mx": Decimal("1000")},
        {"bucket": b0, "requisite_id": 2, "trader_id": 11, "mn": Decimal("500"), "mx": Decimal("2000")},
        {"bucket": b0, "requisite_id": 3, "trader_id": 12, "mn": Decimal("5000"), "mx": Decimal("5500")},
    ]
    monkeypatch.setattr(activity_ch, "query_activity", AsyncMock(return_value=rows))
    monkeypatch.setattr(activity_ch, "available_currencies", AsyncMock(return_value=["RUB"]))

    start = datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 5, 8, 2, 0, tzinfo=timezone.utc)
    out = await service.get_activity_timeseries(start, end, granularity="hour", currency="RUB")

    assert out["granularity"] == "hour"
    assert out["currency"] == "RUB"
    # 00:00, 01:00, 02:00 inclusive; only 00:00 has data.
    assert len(out["buckets"]) == 3
    first = out["buckets"][0]
    assert first["ts"] == b0
    assert first["trader_count"] == 3
    assert [(b["min"], b["max"]) for b in first["bars"]] == [(100.0, 2000.0), (5000.0, 5500.0)]
    assert first["bars"][0]["segments"][1] == {
        "from_amount": 500.0, "to_amount": 1000.0, "requisites": 2,
    }
    # Empty buckets are back-filled with zeros so the axis stays continuous.
    assert out["buckets"][1] == {"ts": _unix(2026, 5, 8, 1), "trader_count": 0, "bars": []}
    assert out["buckets"][2]["bars"] == []


@pytest.mark.asyncio
async def test_compute_activity_defaults_currency_rub_and_prepends_when_missing(service, monkeypatch):
    q = AsyncMock(return_value=[])
    monkeypatch.setattr(activity_ch, "query_activity", q)
    # CH only knows about USDT in-range; the selected RUB must still be offered.
    monkeypatch.setattr(activity_ch, "available_currencies", AsyncMock(return_value=["USDT"]))

    out = await service.get_activity_timeseries(
        datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 5, 8, 1, 0, tzinfo=timezone.utc),
        granularity=None,
        currency=None,
    )

    assert out["currency"] == "RUB"
    assert out["available_currencies"] == ["RUB", "USDT"]
    # Default granularity for a ≤24h span is hourly, and RUB was queried.
    assert out["granularity"] == "hour"
    assert q.await_args.args[2] == "RUB"


def test_bucket_expr_whitelists_minute():
    # The endpoint accepts granularity=minute, so the read query MUST map it to a
    # real bucket expression (else it silently falls back to hour). Whitelisted
    # constant — never user input spliced into SQL.
    assert activity_ch._BUCKET_EXPR["minute"] == "toStartOfMinute(ts)"


@pytest.mark.asyncio
async def test_compute_activity_minute_granularity_buckets_per_minute(service, monkeypatch):
    # Per-minute timeframe (the raw snapshot cadence): buckets advance by 60s and
    # empty minutes are back-filled with zeros just like the coarser grains.
    def _min(mm):
        return int(datetime(2026, 5, 8, 0, mm, tzinfo=timezone.utc).timestamp())

    rows = [
        {"bucket": _min(1), "requisite_id": 1, "trader_id": 10, "mn": Decimal("100"), "mx": Decimal("1000")},
    ]
    monkeypatch.setattr(activity_ch, "query_activity", AsyncMock(return_value=rows))
    monkeypatch.setattr(activity_ch, "available_currencies", AsyncMock(return_value=["RUB"]))

    start = datetime(2026, 5, 8, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 5, 8, 0, 3, tzinfo=timezone.utc)
    out = await service.get_activity_timeseries(start, end, granularity="minute", currency="RUB")

    assert out["granularity"] == "minute"
    # 00:00, 00:01, 00:02, 00:03 inclusive → 4 one-minute buckets, 60s apart.
    ts_list = [b["ts"] for b in out["buckets"]]
    assert ts_list == [_min(0), _min(1), _min(2), _min(3)]
    assert all(b - a == 60 for a, b in zip(ts_list, ts_list[1:]))
    # Only 00:01 carries data; the rest are zero-filled.
    assert out["buckets"][1]["trader_count"] == 1
    assert [(b["min"], b["max"]) for b in out["buckets"][1]["bars"]] == [(100.0, 1000.0)]
    assert out["buckets"][0] == {"ts": _min(0), "trader_count": 0, "bars": []}
    assert out["buckets"][3]["bars"] == []


# ── snapshot_requisite_activity (write path) ─────────────────────────────────

@pytest.mark.asyncio
async def test_snapshot_builds_one_record_per_ready_requisite(service, monkeypatch):
    service.repository.list_ready_requisites_for_snapshot = AsyncMock(return_value=[
        (1, 10, Currency.RUB, Decimal("100"), Decimal("50000"), Decimal("100000")),
        (2, 11, Currency.RUB, Decimal("500"), Decimal("20000"), Decimal("300000")),
    ])
    captured = {}
    monkeypatch.setattr(
        activity_ch, "insert_snapshots",
        lambda recs: captured.setdefault("recs", recs),
    )

    written = await service.snapshot_requisite_activity()

    assert written == 2
    recs = captured["recs"]
    assert [r.requisite_id for r in recs] == [1, 2]
    assert {r.currency for r in recs} == {"RUB"}  # enum → .value
    # One shared tick timestamp for the whole batch (so a bucket groups cleanly).
    assert len({r.ts for r in recs}) == 1
    assert recs[0].ts.microsecond == 0


@pytest.mark.asyncio
async def test_snapshot_noop_when_nothing_ready(service, monkeypatch):
    service.repository.list_ready_requisites_for_snapshot = AsyncMock(return_value=[])
    called = MagicMock()
    monkeypatch.setattr(activity_ch, "insert_snapshots", called)

    written = await service.snapshot_requisite_activity()

    assert written == 0
    called.assert_not_called()
