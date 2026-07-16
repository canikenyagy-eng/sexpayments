"""Unit tests for provider request logging → ClickHouse.

Covers: record routing (in-request buffer vs background emit), header masking,
body truncation, fail-safe; AsyncBatchSink (flush / overflow / CH-error / aclose);
SyncDirectSink; and the request_signed capture-then-reraise contract.
"""
import json
import pathlib
import types
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.core.context import provider_requests_var
from app.infrastructure.clickhouse import sink as s
from app.modules.cascading import repository as rl
from app.modules.cascading.integrations.base import ProviderAdapter


def _provider(pid=7, code="swifty"):
    return types.SimpleNamespace(id=pid, code=code, base_url="https://prov.example", request_timeout_ms=5000)


# ── record_provider_request routing ───────────────────────────────────────


def test_record_routes_to_buffer_when_in_request():
    """Inside a merchant request (contextvar holds a list) → appended, NOT emitted."""
    buf: list = []
    token = provider_requests_var.set(buf)
    try:
        with patch.object(rl, "emit_record") as emit:
            rl.record_provider_request(
                provider=_provider(), method="POST", url="https://prov.example/pay",
                headers={"Authorization": "Bearer secret-token", "Content-Type": "application/json"},
                body='{"a":1}', status=200, response_body='{"ok":true}',
                success=True, error="", provider_latency_ms=42,
            )
        assert len(buf) == 1
        emit.assert_not_called()
        rec = buf[0]
        assert rec.response_status == 200 and rec.success is True
        assert rec.provider_latency_ms == 42
    finally:
        provider_requests_var.reset(token)


def test_record_emits_when_background():
    """No request context (contextvar None) → emitted immediately."""
    provider_requests_var.set(None)
    with patch.object(rl, "emit_record") as emit:
        rl.record_provider_request(
            provider=_provider(), method="GET", url="https://prov.example/balance",
            headers={}, body="", status=500, response_body="err",
            success=False, error="boom", provider_latency_ms=10,
        )
    emit.assert_called_once()
    rec = emit.call_args[0][0]
    assert rec.success is False and rec.response_status == 500 and rec.error == "boom"


def test_record_masks_sensitive_headers():
    provider_requests_var.set(None)
    with patch.object(rl, "emit_record") as emit:
        rl.record_provider_request(
            provider=_provider(), method="POST", url="u",
            headers={"Authorization": "Bearer supersecretvalue", "X-Signature": "abcdef123456", "Content-Type": "application/json"},
            body="{}", status=200, response_body="{}", success=True, error="", provider_latency_ms=1,
        )
    hdrs = json.loads(emit.call_args[0][0].request_headers)
    assert hdrs["Content-Type"] == "application/json"
    assert "supersecretvalue" not in hdrs["Authorization"] and hdrs["Authorization"].startswith("Bearer")
    assert "abcdef123456" not in hdrs["X-Signature"]


def test_record_truncates_large_bodies():
    provider_requests_var.set(None)
    big = "x" * (rl.MAX_BODY + 5000)
    with patch.object(rl, "emit_record") as emit:
        rl.record_provider_request(
            provider=_provider(), method="POST", url="u", headers={}, body=big,
            status=200, response_body=big, success=True, error="", provider_latency_ms=1,
        )
    rec = emit.call_args[0][0]
    assert len(rec.request_body) == rl.MAX_BODY
    assert len(rec.response_body) == rl.MAX_BODY


def test_record_never_raises():
    provider_requests_var.set(None)
    # emit raising must be swallowed.
    with patch.object(rl, "emit_record", side_effect=RuntimeError("x")):
        rl.record_provider_request(
            provider=_provider(), method="POST", url="u", headers={}, body="{}",
            status=200, response_body="{}", success=True, error="", provider_latency_ms=1,
        )  # no exception


def test_as_row_matches_columns():
    rec = rl.ProviderRequestRecord(
        provider_id="1", provider_code="c", method="POST", url="u",
        request_headers="{}", request_body="{}", response_status=200,
        response_body="{}", success=True, error="", provider_latency_ms=5,
    )
    assert len(rec.as_row()) == len(rl.COLUMNS)


def test_record_carries_request_type():
    """request_type flows onto the record (and its row)."""
    provider_requests_var.set(None)
    with patch.object(rl, "emit_record") as emit:
        rl.record_provider_request(
            provider=_provider(), method="POST", url="u", headers={}, body="{}",
            status=200, response_body="{}", success=True, error="",
            provider_latency_ms=1, request_type="cancel",
        )
    rec = emit.call_args[0][0]
    assert rec.request_type == "cancel"
    assert "cancel" in rec.as_row()


def test_record_request_type_defaults_to_other():
    provider_requests_var.set(None)
    with patch.object(rl, "emit_record") as emit:
        rl.record_provider_request(
            provider=_provider(), method="GET", url="u", headers={}, body="",
            status=200, response_body="", success=True, error="", provider_latency_ms=1,
        )
    assert emit.call_args[0][0].request_type == "other"


def test_provider_request_type_scope_sets_and_resets():
    """The scope the high-level caller uses to tag every request_signed call."""
    from app.modules.cascading.integrations.base import (
        _PROVIDER_REQUEST_TYPE,
        provider_request_type,
    )

    assert _PROVIDER_REQUEST_TYPE.get() == "other"
    with provider_request_type("balance"):
        assert _PROVIDER_REQUEST_TYPE.get() == "balance"
    assert _PROVIDER_REQUEST_TYPE.get() == "other"  # reset after the scope


# ── Sinks ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_async_batch_sink_flushes_buffer():
    sink = s.AsyncBatchSink(batch_size=1000, flush_interval_s=3600)
    try:
        with patch.object(s, "_insert_rows") as ins:
            for _ in range(3):
                sink.emit(rl.ProviderRequestRecord(
                    provider_id="1", provider_code="c", method="POST", url="u",
                    request_headers="{}", request_body="{}", response_status=200,
                    response_body="{}", success=True, error="", provider_latency_ms=1,
                ))
            assert ins.call_count == 0  # below batch_size, not flushed yet
            await sink._flush()
            ins.assert_called_once()
            assert ins.call_args[0][0] == "provider_requests"   # table
            assert len(ins.call_args[0][2]) == 3  # rows (table, columns, rows)
    finally:
        await sink.aclose()


@pytest.mark.asyncio
async def test_async_batch_sink_failsafe_on_ch_error():
    sink = s.AsyncBatchSink(batch_size=1000, flush_interval_s=3600)
    try:
        with patch.object(s, "_insert_rows", side_effect=RuntimeError("CH down")):
            sink.emit(rl.ProviderRequestRecord(
                provider_id="1", provider_code="c", method="POST", url="u",
                request_headers="{}", request_body="{}", response_status=200,
                response_body="{}", success=True, error="", provider_latency_ms=1,
            ))
            await sink._flush()  # must not raise
    finally:
        await sink.aclose()


@pytest.mark.asyncio
async def test_async_batch_sink_overflow_drops_oldest():
    sink = s.AsyncBatchSink(batch_size=10_000, flush_interval_s=3600, max_buffer=2)
    try:
        for i in range(4):
            sink.emit(rl.ProviderRequestRecord(
                provider_id=str(i), provider_code="c", method="POST", url="u",
                request_headers="{}", request_body="{}", response_status=200,
                response_body="{}", success=True, error="", provider_latency_ms=1,
            ))
        assert len(sink._buf) == 2
        assert sink._dropped == 2
    finally:
        await sink.aclose()


@pytest.mark.asyncio
async def test_async_batch_sink_aclose_flushes_remainder():
    sink = s.AsyncBatchSink(batch_size=1000, flush_interval_s=3600)
    with patch.object(s, "_insert_rows") as ins:
        sink.emit(rl.ProviderRequestRecord(
            provider_id="1", provider_code="c", method="POST", url="u",
            request_headers="{}", request_body="{}", response_status=200,
            response_body="{}", success=True, error="", provider_latency_ms=1,
        ))
        await sink.aclose()
        ins.assert_called_once()


def test_sync_direct_sink_inserts_immediately():
    sink = s.SyncDirectSink()
    with patch.object(s, "_insert_rows") as ins:
        sink.emit(rl.ProviderRequestRecord(
            provider_id="1", provider_code="c", method="POST", url="u",
            request_headers="{}", request_body="{}", response_status=200,
            response_body="{}", success=True, error="", provider_latency_ms=1,
        ))
    ins.assert_called_once()
    assert len(ins.call_args[0][2]) == 1  # (table, columns, rows)


def test_sync_direct_sink_failsafe():
    sink = s.SyncDirectSink()
    with patch.object(s, "_insert_rows", side_effect=RuntimeError("CH down")):
        sink.emit(rl.ProviderRequestRecord(
            provider_id="1", provider_code="c", method="POST", url="u",
            request_headers="{}", request_body="{}", response_status=200,
            response_body="{}", success=True, error="", provider_latency_ms=1,
        ))  # no exception


# ── request_signed capture-then-reraise contract ───────────────────────────


class _FakeSelf:
    """Minimal stand-in providing what ProviderAdapter.request_signed calls,
    without instantiating the abstract adapter."""
    code = "fake"

    async def acquire_token(self, provider):
        return "tok"

    def sign_request(self, **kw):
        return {"Authorization": "Bearer secret", "Content-Type": "application/json"}

    def serialize_request_body(self, body):
        return json.dumps(body, sort_keys=True, separators=(",", ":"))


def _fake_httpx_client(resp=None, raise_exc=None):
    """Return a fake httpx.AsyncClient async-context-manager class."""
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, path, **kwargs):
            if raise_exc is not None:
                raise raise_exc
            return resp

    return _Client


@pytest.mark.asyncio
async def test_request_signed_logs_success_and_returns():
    provider_requests_var.set(None)
    resp = MagicMock()
    resp.status_code = 201
    resp.text = '{"requisite":"x"}'
    captured = {}

    def _rec(**kw):
        captured.update(kw)

    with patch("app.modules.cascading.integrations.base.httpx.AsyncClient", _fake_httpx_client(resp=resp)), \
         patch("app.modules.cascading.repository.record_provider_request", _rec):
        out = await ProviderAdapter.request_signed(
            _FakeSelf(), provider=_provider(), method="post", path="/v1/pay", body={"a": 1},
        )
    assert out is resp
    assert captured["success"] is True and captured["status"] == 201
    assert captured["response_body"] == '{"requisite":"x"}'
    assert captured["url"].endswith("/v1/pay")
    assert captured["provider_latency_ms"] >= 0


@pytest.mark.asyncio
async def test_request_signed_logs_error_and_reraises():
    provider_requests_var.set(None)
    captured = {}

    def _rec(**kw):
        captured.update(kw)

    err = httpx.ConnectError("boom")
    with patch("app.modules.cascading.integrations.base.httpx.AsyncClient", _fake_httpx_client(raise_exc=err)), \
         patch("app.modules.cascading.repository.record_provider_request", _rec):
        with pytest.raises(httpx.RequestError):
            await ProviderAdapter.request_signed(
                _FakeSelf(), provider=_provider(), method="post", path="/v1/pay", body={"a": 1},
            )
    assert captured["success"] is False and captured["status"] == 0
    assert "boom" in captured["error"]


# ── query_provider_requests (admin page read path) ─────────────────────────


@pytest.mark.asyncio
async def test_query_returns_empty_when_ch_disabled():
    with patch.object(rl, "get_settings", return_value=types.SimpleNamespace(CLICKHOUSE_ENABLED=False)):
        out = await rl.query_provider_requests(limit=10)
    assert out == []


@pytest.mark.asyncio
async def test_query_builds_sql_and_maps_rows():
    captured = {}

    class _Res:
        column_names = ["provider_code", "success"]
        result_rows = [("swifty", 1), ("garex", 0)]

    fake_client = MagicMock()

    def _query(sql, parameters=None):
        captured["sql"] = sql
        captured["params"] = parameters
        return _Res()

    fake_client.query = _query

    with patch.object(rl, "get_settings", return_value=types.SimpleNamespace(CLICKHOUSE_ENABLED=True)), \
         patch.object(rl.ch, "get_client", return_value=fake_client):
        out = await rl.query_provider_requests(
            limit=5, offset=10, provider_code="swifty", success=False, order_id="7",
        )

    assert captured["params"] == {"pc": "swifty", "s": 0, "oid": "7"}
    assert "provider_code = {pc:String}" in captured["sql"]
    assert "ORDER BY ts DESC LIMIT 5 OFFSET 10" in captured["sql"]
    assert out == [{"provider_code": "swifty", "success": 1}, {"provider_code": "garex", "success": 0}]


@pytest.mark.asyncio
async def test_query_failsafe_returns_empty_on_error():
    fake_client = MagicMock()
    fake_client.query = MagicMock(side_effect=RuntimeError("CH down"))
    with patch.object(rl, "get_settings", return_value=types.SimpleNamespace(CLICKHOUSE_ENABLED=True)), \
         patch.object(rl.ch, "get_client", return_value=fake_client):
        out = await rl.query_provider_requests(limit=5)
    assert out == []
    

# ── inbound provider callbacks (CH) ─────────────────────────────────────────


def test_callback_as_row_matches_columns():
    rec = rl.ProviderCallbackRecord(
        provider_id="1", provider_code="c", order_id="2", external_order_id="e",
        signature_valid=True, parsed_status="paid", request_headers="{}",
        request_body="{}", response_status=200, response_body="{}", error="",
        processing_ms=5,
    )
    assert len(rec.as_row()) == len(rl.CALLBACK_COLUMNS)
    assert rec.table == "provider_callbacks"
    assert rec.columns == rl.CALLBACK_COLUMNS


def test_record_callback_emits_and_masks():
    with patch.object(rl, "emit_record") as emit:
        rl.record_provider_callback(
            provider_code="swifty", provider_id=7,
            headers={"X-Signature": "abcdef123456", "Content-Type": "application/json"},
            body='{"status":"paid"}', signature_valid=True, parsed_status="paid",
            external_order_id="ext-1", order_id=42, response_status=200,
            response_body='{"ok":true}', error="", processing_ms=12,
        )
    emit.assert_called_once()
    rec = emit.call_args[0][0]
    assert isinstance(rec, rl.ProviderCallbackRecord)
    assert rec.table == "provider_callbacks"
    assert rec.provider_code == "swifty" and rec.provider_id == "7"
    assert rec.order_id == "42" and rec.external_order_id == "ext-1"
    assert rec.signature_valid is True and rec.parsed_status == "paid"
    assert rec.processing_ms == 12
    hdrs = json.loads(rec.request_headers)
    assert "abcdef123456" not in hdrs["X-Signature"]
    assert hdrs["Content-Type"] == "application/json"


def test_record_callback_never_raises():
    with patch.object(rl, "emit_record", side_effect=RuntimeError("x")):
        rl.record_provider_callback(
            provider_code="c", provider_id=1, headers={}, body="{}",
            signature_valid=False, parsed_status=None, external_order_id=None,
            order_id=None, response_status=500, response_body="{}",
            error="boom", processing_ms=1,
        )  # no exception


@pytest.mark.asyncio
async def test_query_callbacks_returns_empty_when_ch_disabled():
    with patch.object(rl, "get_settings", return_value=types.SimpleNamespace(CLICKHOUSE_ENABLED=False)):
        out = await rl.query_provider_callbacks(limit=10)
    assert out == []


@pytest.mark.asyncio
async def test_query_callbacks_builds_sql_and_maps_rows():
    captured = {}

    class _Res:
        column_names = list(rl.CALLBACK_COLUMNS)
        result_rows = [(
            "2026-06-06T00:00:00", "rid-1", "7", "swifty", "42", "ext-1", 1, "paid",
            '{"X-Signature": "ab… (12 chars)"}', '{"status":"paid"}', 200,
            '{"ok":true}', "", 15,
        )]

    fake_client = MagicMock()

    def _query(sql, parameters=None):
        captured["sql"] = sql
        captured["params"] = parameters
        return _Res()

    fake_client.query = _query

    with patch.object(rl, "get_settings", return_value=types.SimpleNamespace(CLICKHOUSE_ENABLED=True)), \
         patch.object(rl.ch, "get_client", return_value=fake_client):
        out = await rl.query_provider_callbacks(
            limit=5, offset=10, provider_code="swifty", order_id=42,
            external_order_id="ext-1", signature_valid=True,
        )

    assert captured["params"] == {"pc": "swifty", "oid": "42", "eoid": "ext-1", "sv": 1}
    assert "FROM provider_callbacks" in captured["sql"]
    assert "ORDER BY ts DESC LIMIT 5 OFFSET 10" in captured["sql"]
    row = out[0]
    assert row["provider_id"] == 7 and row["order_id"] == 42       # str → int
    assert row["signature_valid"] is True                          # UInt8 → bool
    assert row["created_at"] == "2026-06-06T00:00:00"              # ts → created_at
    assert row["error_message"] is None                            # "" → None
    assert isinstance(row["request_headers"], dict)               # JSON → object
    assert row["response_status"] == 200 and row["processing_ms"] == 15
    assert row["request_id"] == "rid-1"


_BACKEND_ROOT = pathlib.Path(__file__).resolve().parents[2]
_CH_DDL_FILES = [
    "app/infrastructure/clickhouse/schema/cascading.sql",
    "app/infrastructure/clickhouse/schema/audit.sql",
    "app/infrastructure/clickhouse/schema/selector.sql",
]


@pytest.mark.parametrize("rel", _CH_DDL_FILES)
def test_clickhouse_ddl_partition_before_order(rel):
    text = (_BACKEND_ROOT / rel).read_text()
    seen = 0
    for stmt in text.split(";"):
        if "ENGINE = MergeTree" not in stmt:
            continue
        if "PARTITION BY" in stmt and "ORDER BY" in stmt:
            seen += 1
            assert stmt.index("PARTITION BY") < stmt.index("ORDER BY"), (
                f"{rel}: PARTITION BY must precede ORDER BY in a MergeTree CREATE TABLE"
            )
    assert seen > 0, f"{rel}: no MergeTree table with PARTITION BY + ORDER BY found"


def test_ensure_schema_ignores_semicolons_in_comments(tmp_path):
    """A ';' inside a -- comment must not be treated as a statement separator.

    Regression: a comment like ``(batched inserts; never per-row)`` split the
    DDL into a comment-only fragment, which CH rejected (code 62), so the whole
    bootstrap failed and no table was ever created.
    """
    from app.infrastructure.clickhouse import client as ch_client

    ddl = tmp_path / "schema.sql"
    ddl.write_text(
        "-- header with a semicolon; right here inside a comment\n"
        "CREATE TABLE a (x UInt8) ENGINE = Memory;\n"
        "\n"
        "-- another; comment with ; semicolons\n"
        "CREATE TABLE b (y UInt8) ENGINE = Memory;  -- trailing; comment\n"
    )
    sent: list = []
    fake = MagicMock()
    fake.command = lambda stmt: sent.append(stmt)
    with patch.object(ch_client, "get_client", return_value=fake):
        ch_client.ensure_schema(ddl)

    assert len(sent) == 2, sent  # NOT split by the comment semicolons
    assert all(s.lstrip().upper().startswith("CREATE TABLE") for s in sent)
    assert " a " in sent[0] and " b " in sent[1]
