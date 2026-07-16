"""Unit tests for merchant API logs + order-creation snapshots → ClickHouse.

Covers record shape/emit/fail-safe, disabled-CH fall-through, and the SQL/param
building + row mapping for the audit-page list, snapshot lookup, order-debug,
and the migrated statistics.
"""
import types
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from app.modules.audit import repository as cl


def _enabled():
    return types.SimpleNamespace(CLICKHOUSE_ENABLED=True)


def _disabled():
    return types.SimpleNamespace(CLICKHOUSE_ENABLED=False)


def _client(cap, column_names=None, rows=None):
    fake = MagicMock()

    def _query(sql, parameters=None):
        cap["sql"] = sql
        cap["params"] = parameters
        res = MagicMock()
        res.column_names = column_names or []
        res.result_rows = rows or []
        return res

    fake.query = _query
    return fake


# ── records ─────────────────────────────────────────────────────────────────


def test_log_record_row_matches_columns():
    rec = cl.MerchantApiLogRecord(
        request_id="r", merchant_id=1, order_id="2", url="/u", method="POST",
        request_headers="{}", request_body="{}", response_status=200,
        response_headers="{}", response_body="{}", response_time_ms=5,
    )
    assert len(rec.as_row()) == len(cl.LOG_COLUMNS)
    assert rec.table == "merchant_api_logs" and rec.columns == cl.LOG_COLUMNS


def test_snap_record_row_matches_columns():
    rec = cl.OrderCreationSnapshotRecord(
        request_id="r", merchant_id=1, order_id="2", response_time_ms=5,
        request_data="{}", merchant_snapshot="{}", rate_snapshot="",
        traders_snapshot="[]", candidates="[]", result="{}",
    )
    assert len(rec.as_row()) == len(cl.SNAP_COLUMNS)
    assert rec.table == "order_creation_snapshots" and rec.columns == cl.SNAP_COLUMNS


def test_record_merchant_api_log_emits():
    with patch.object(cl, "emit_record") as emit:
        cl.record_merchant_api_log(
            request_id="req-1", merchant_id=7, order_id=42,
            url="/api/merchant/v1/orders/payin", method="POST",
            request_headers={"a": 1}, request_body='{"amount":5000}',
            response_status=201, response_headers={"x": "y"},
            response_body='{"id":"u"}', response_time_ms=12,
        )
    emit.assert_called_once()
    rec = emit.call_args[0][0]
    assert isinstance(rec, cl.MerchantApiLogRecord)
    assert rec.request_id == "req-1" and rec.merchant_id == 7 and rec.order_id == "42"
    assert rec.method == "POST"


def test_record_snapshot_serializes_none_rate():
    with patch.object(cl, "emit_record") as emit:
        cl.record_order_creation_snapshot(
            request_id="req-1", merchant_id=7, order_id=42, response_time_ms=12,
            snapshot={
                "request_data": {"amount": 5000}, "result": {"success": True},
                "candidates": [{"id": 1}], "traders_snapshot": [],
                "merchant_snapshot": {"id": 7}, "rate_snapshot": None,
            },
        )
    rec = emit.call_args[0][0]
    assert isinstance(rec, cl.OrderCreationSnapshotRecord)
    assert rec.rate_snapshot == ""                       # None → ''
    assert rec.result.replace(" ", "") == '{"success":true}'


def test_record_never_raises():
    with patch.object(cl, "emit_record", side_effect=RuntimeError("x")):
        cl.record_merchant_api_log(
            request_id="r", merchant_id=1, order_id=None, url="u", method="POST",
            request_headers={}, request_body="", response_status=500,
            response_headers={}, response_body="", response_time_ms=1,
        )  # no exception


# ── disabled CH → graceful defaults ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_reads_return_defaults_when_disabled():
    with patch.object(cl, "get_settings", _disabled):
        assert await cl.list_merchant_api_logs(limit=10) == []
        assert await cl.get_snapshot_by_request_id("r") is None
        assert await cl.list_logs_for_order(1) == []
        assert await cl.get_latest_snapshot_for_order(1) is None
        assert await cl.count_payin_requests() == 0
        assert await cl.sum_payin_amount() == 0.0
        assert await cl.failed_payin_amount() == 0.0
        assert await cl.volume_distribution_24h() == []
        assert await cl.list_order_creation_requests() == ([], 0)


# ── statistics SQL/params ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_count_payin_requests_sql_and_params():
    cap = {}
    with patch.object(cl, "get_settings", _enabled), \
         patch.object(cl.ch, "get_client", return_value=_client(cap, ["c"], [(5,)])):
        out = await cl.count_payin_requests(
            merchant_id=7, date_from=datetime(2026, 1, 1), date_to=datetime(2026, 2, 1),
        )
    assert out == 5
    assert "method = 'POST'" in cap["sql"] and "/orders/payin" in cap["sql"]
    assert "merchant_id = {mid:Int64}" in cap["sql"]
    assert cap["params"]["mid"] == 7
    assert "df" in cap["params"] and "dt" in cap["params"]


@pytest.mark.asyncio
async def test_sum_payin_amount_since():
    cap = {}
    with patch.object(cl, "get_settings", _enabled), \
         patch.object(cl.ch, "get_client", return_value=_client(cap, ["s"], [(999.5,)])):
        out = await cl.sum_payin_amount(since=datetime(2026, 6, 1))
    assert out == 999.5
    assert "JSONExtractFloat(request_body, 'amount')" in cap["sql"]
    assert "ts > {since:DateTime64(3)}" in cap["sql"]
    assert "since" in cap["params"]


@pytest.mark.asyncio
async def test_failed_payin_amount_filters():
    cap = {}
    with patch.object(cl, "get_settings", _enabled), \
         patch.object(cl.ch, "get_client", return_value=_client(cap, ["s"], [(123.5,)])):
        out = await cl.failed_payin_amount()
    assert out == 123.5
    assert "order_id = ''" in cap["sql"]
    assert "response_status >= 400" in cap["sql"]
    assert "url LIKE '%/payin%'" in cap["sql"]


@pytest.mark.asyncio
async def test_volume_distribution_buckets():
    cap = {}
    rows = [("sbp", 100.0, 200.0, 0.0, 0.0, 0.0, 0.0)]
    with patch.object(cl, "get_settings", _enabled), \
         patch.object(cl.ch, "get_client", return_value=_client(
             cap, ["payment_method", "lt_1000", "from_1000", "from_5000",
                   "from_8000", "from_10000", "from_20000"], rows)):
        out = await cl.volume_distribution_24h()
    assert "GROUP BY payment_method" in cap["sql"]
    assert "INTERVAL 24 HOUR" in cap["sql"]
    assert out == [{
        "method": "sbp", "lt_1000": 100.0, "from_1000": 200.0,
        "from_5000": 0.0, "from_8000": 0.0, "from_10000": 0.0, "from_20000": 0.0,
    }]


# ── audit list + mapping ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_logs_endpoint_group_bot_and_mapping():
    cap = {}
    row = ("2026-06-06T00:00:00", "req-1", 7, "42", "/api/bot/v1/x", "POST",
           '{"h":1}', '{"amount":5000}', 200, '{}', '{"ok":true}', 12)
    with patch.object(cl, "get_settings", _enabled), \
         patch.object(cl.ch, "get_client", return_value=_client(cap, cl.LOG_COLUMNS, [row])):
        out = await cl.list_merchant_api_logs(
            endpoint_group="bot", method="post", merchant_id=7,
            sort_by="created_at", sort_order="asc",
        )
    assert cap["params"]["gbot"] == "/api/bot/v1/%"
    assert cap["params"]["method"] == "POST"
    assert "ORDER BY ts ASC" in cap["sql"]
    item = out[0]
    assert item["request_id"] == "req-1" and item["merchant_id"] == 7
    assert item["order_id"] == 42 and item["response_status"] == 200
    assert isinstance(item["request_headers"], dict)
    assert item["created_at"] == "2026-06-06T00:00:00"


@pytest.mark.asyncio
async def test_list_logs_endpoint_group_unknown_negations():
    cap = {}
    with patch.object(cl, "get_settings", _enabled), \
         patch.object(cl.ch, "get_client", return_value=_client(cap, cl.LOG_COLUMNS, [])):
        await cl.list_merchant_api_logs(endpoint_group="unknown")
    # 6 NOT LIKE negation params (5 merchant prefixes + bot)
    assert sum(1 for k in cap["params"] if k.startswith("nu")) == 6
    assert cap["sql"].count("NOT LIKE") == 6


@pytest.mark.asyncio
async def test_list_order_creation_requests_maps_and_counts():
    cap = {"sqls": []}

    fake = MagicMock()

    def _query(sql, parameters=None):
        cap["sqls"].append(sql)
        res = MagicMock()
        if "count()" in sql:
            res.column_names = ["c"]
            res.result_rows = [(3,)]
        else:
            res.column_names = ["request_id", "merchant_id", "response_time_ms",
                                "request_data", "result", "ts"]
            res.result_rows = [(
                "req-9", 7, 15, '{"amount":5000,"method":"sbp"}',
                '{"success":true}', "2026-06-06T00:00:00",
            )]
        return res

    fake.query = _query
    with patch.object(cl, "get_settings", _enabled), \
         patch.object(cl.ch, "get_client", return_value=fake):
        rows, total = await cl.list_order_creation_requests(skip=0, limit=50)
    assert total == 3
    assert rows[0]["request_id"] == "req-9" and rows[0]["merchant_id"] == 7
    assert rows[0]["request_data"]["amount"] == 5000
    assert rows[0]["result"]["success"] is True


def test_order_request_log_response_accepts_uuid_id():
    """Regression: list_order_creation_requests now sets id=request_id (a UUID
    string), so the response schema must accept a string id — an `int` field
    would 500 the /admin/order-requests endpoint at serialization."""
    from app.modules.stats.schemas import (
        OrderRequestLogResponse,
        PaginatedOrderRequestsResponse,
    )

    item = OrderRequestLogResponse(
        id="3f8a2b1c-0000-4000-8000-000000000000",
        merchant_id=7, merchant_login="acme", amount_rub=5000.0,
        method="sbp", success=True, response_time_ms=12,
        created_at=datetime(2026, 6, 6, 0, 0, 0),
    )
    assert item.id == "3f8a2b1c-0000-4000-8000-000000000000"
    page = PaginatedOrderRequestsResponse(items=[item], total=1)
    assert page.total == 1 and page.items[0].merchant_login == "acme"
