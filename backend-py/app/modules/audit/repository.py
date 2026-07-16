"""Audit data access.

``AuditLogRepository`` is the Postgres ``audit_logs`` repository (transactional
compliance trail — stays in PG). The rest of this module is the ClickHouse access
for merchant/bot API logs + order-creation snapshots (high-volume, moved off PG):
records are delivered via the shared batched sink
(``app.infrastructure.clickhouse.sink``); DDL lives in
``app/infrastructure/clickhouse/schema/audit.sql``.

Design notes:
  * The two CH tables are linked by ``request_id`` (CH has no autoincrement id),
    and ``merchant_id`` / ``order_id`` / ``response_time_ms`` are denormalized
    into the snapshot so **no cross-table CH join is ever needed**.
  * Reads are fail-safe: ``[]`` / ``0`` / ``None`` when CH is disabled or on
    error. All filters are server-bound (``{name:Type}``); the only inline SQL
    fragments are constants (URL/method patterns), never user input.
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.types import utcnow
from app.core.config import get_settings
from app.infrastructure.clickhouse import client as ch
from app.infrastructure.clickhouse.sink import MAX_BODY, emit_record
from app.modules.audit.models import AuditLog
from app.modules.base.repository import BaseRepository

logger = logging.getLogger("audit.repository")


class AuditLogRepository(BaseRepository[AuditLog]):
    def __init__(self, session: AsyncSession):
        super().__init__(AuditLog, session)


# ─────────────────────────────────────────────────────────────────────────────
# ClickHouse: merchant/bot API logs + order-creation snapshots
# ─────────────────────────────────────────────────────────────────────────────

_LOG_TABLE = "merchant_api_logs"
_SNAP_TABLE = "order_creation_snapshots"

LOG_COLUMNS = [
    "ts", "request_id", "merchant_id", "order_id", "url", "method",
    "request_headers", "request_body", "response_status", "response_headers",
    "response_body", "response_time_ms",
]
SNAP_COLUMNS = [
    "ts", "request_id", "merchant_id", "order_id", "response_time_ms",
    "request_data", "merchant_snapshot", "rate_snapshot", "traders_snapshot",
    "candidates", "result",
]

# Matches the two payin-create channels (external API + bot). Constant SQL.
_PAYIN_CREATE = (
    "(url LIKE '%/orders/payin' OR match(url, '^/api/bot/v1/merchants/[0-9]+/orders$'))"
)
_AMOUNT = "JSONExtractFloat(request_body, 'amount')"
_METHOD = (
    "coalesce(nullIf(JSONExtractString(request_body,'payment_method'),''), "
    "nullIf(JSONExtractString(request_body,'method'),''), 'unknown')"
)

# Granularity → constant bucket-start expression (whitelisted; the service only
# ever passes one of these keys, never user input). Unlike requisite_activity_
# snapshots.ts (pinned DateTime64(3,'UTC')), merchant_api_logs.ts is a bare
# DateTime64(3), so we force 'UTC' in every truncation — otherwise a non-UTC CH
# server would bucket in local time and misalign against the reader's aware-UTC
# walker (records are written with utcnow()).
_BUCKET_EXPR = {
    "hour": "toStartOfHour(ts, 'UTC')",
    "3hour": "toStartOfInterval(ts, INTERVAL 3 HOUR, 'UTC')",
    "day": "toStartOfDay(ts, 'UTC')",
    "week": "toMonday(ts, 'UTC')",
    "month": "toStartOfMonth(ts, 'UTC')",
}


# ── records (write path) ────────────────────────────────────────────────────


@dataclass
class MerchantApiLogRecord:
    request_id: str
    merchant_id: int
    order_id: str
    url: str
    method: str
    request_headers: str          # JSON
    request_body: str
    response_status: int
    response_headers: str         # JSON
    response_body: str
    response_time_ms: int
    ts: datetime = field(default_factory=utcnow)

    def as_row(self) -> list:
        return [
            self.ts, self.request_id, int(self.merchant_id or 0), self.order_id,
            self.url, self.method, self.request_headers, self.request_body,
            int(self.response_status or 0), self.response_headers,
            self.response_body, int(self.response_time_ms or 0),
        ]


@dataclass
class OrderCreationSnapshotRecord:
    request_id: str
    merchant_id: int
    order_id: str
    response_time_ms: int
    request_data: str
    merchant_snapshot: str
    rate_snapshot: str
    traders_snapshot: str
    candidates: str
    result: str
    ts: datetime = field(default_factory=utcnow)

    def as_row(self) -> list:
        return [
            self.ts, self.request_id, int(self.merchant_id or 0), self.order_id,
            int(self.response_time_ms or 0), self.request_data,
            self.merchant_snapshot, self.rate_snapshot, self.traders_snapshot,
            self.candidates, self.result,
        ]


MerchantApiLogRecord.table = _LOG_TABLE
MerchantApiLogRecord.columns = LOG_COLUMNS
OrderCreationSnapshotRecord.table = _SNAP_TABLE
OrderCreationSnapshotRecord.columns = SNAP_COLUMNS


def _dump(obj: Any) -> str:
    try:
        return json.dumps(obj, default=str, ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return ""


def record_merchant_api_log(
    *,
    request_id: Optional[str],
    merchant_id: Optional[int],
    order_id: Optional[int],
    url: str,
    method: str,
    request_headers: Any,
    request_body: Optional[str],
    response_status: int,
    response_headers: Any,
    response_body: Optional[str],
    response_time_ms: int,
) -> None:
    """Emit one merchant/bot API request log to ClickHouse. Never raises."""
    try:
        rec = MerchantApiLogRecord(
            request_id=str(request_id or ""),
            merchant_id=int(merchant_id or 0),
            order_id=str(order_id) if order_id is not None else "",
            url=url or "",
            method=method or "",
            request_headers=_dump(request_headers or {})[:MAX_BODY],
            request_body=(request_body or "")[:MAX_BODY],
            response_status=int(response_status or 0),
            response_headers=_dump(response_headers or {})[:MAX_BODY],
            response_body=(response_body or "")[:MAX_BODY],
            response_time_ms=int(response_time_ms or 0),
        )
        emit_record(rec)
    except Exception as exc:  # noqa: BLE001 — logging must never break a request
        logger.warning("record_merchant_api_log failed: %s", exc)


def record_order_creation_snapshot(
    *,
    request_id: Optional[str],
    merchant_id: Optional[int],
    order_id: Optional[int],
    response_time_ms: int,
    snapshot: Dict[str, Any],
) -> None:
    """Emit one order-creation snapshot to ClickHouse. Never raises."""
    try:
        rate = snapshot.get("rate_snapshot")
        rec = OrderCreationSnapshotRecord(
            request_id=str(request_id or ""),
            merchant_id=int(merchant_id or 0),
            order_id=str(order_id) if order_id is not None else "",
            response_time_ms=int(response_time_ms or 0),
            request_data=_dump(snapshot.get("request_data", {})),
            merchant_snapshot=_dump(snapshot.get("merchant_snapshot", {})),
            rate_snapshot=_dump(rate) if rate is not None else "",
            traders_snapshot=_dump(snapshot.get("traders_snapshot", [])),
            candidates=_dump(snapshot.get("candidates", [])),
            result=_dump(snapshot.get("result", {})),
        )
        emit_record(rec)
    except Exception as exc:  # noqa: BLE001
        logger.warning("record_order_creation_snapshot failed: %s", exc)


# ── read path (executor + fail-safe) ────────────────────────────────────────


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


async def _fetch(sql: str, params: Dict[str, Any]) -> Optional[Tuple[list, list]]:
    if not get_settings().CLICKHOUSE_ENABLED:
        return None

    def _run():
        # Serialize access to the non-thread-safe shared sync client.
        with ch.query_lock:
            res = ch.get_client().query(sql, parameters=params)
            return res.column_names, res.result_rows

    try:
        return await asyncio.get_running_loop().run_in_executor(None, _run)
    except Exception as exc:  # noqa: BLE001 — reads must not 500 the page/stats
        logger.warning("%s query failed: %s", _LOG_TABLE, exc)
        return None


async def _scalar(sql: str, params: Dict[str, Any], default: Any) -> Any:
    out = await _fetch(sql, params)
    if not out:
        return default
    _, rows = out
    if not rows or rows[0][0] is None:
        return default
    return rows[0][0]


def _loads(s: Any, default: Any) -> Any:
    if not s:
        return default
    try:
        return json.loads(s)
    except Exception:  # noqa: BLE001
        return default


def _as_int_or_none(v: Any) -> Optional[int]:
    s = str(v if v is not None else "")
    return int(s) if (s.isdigit() and int(s) > 0) else None


def _log_row(names: list, row: tuple) -> Dict[str, Any]:
    d = dict(zip(names, row))
    rs = d.get("response_status")
    return {
        "request_id": d.get("request_id") or None,
        "merchant_id": _as_int_or_none(d.get("merchant_id")),
        "order_id": _as_int_or_none(d.get("order_id")),
        "url": d.get("url") or "",
        "method": d.get("method") or "",
        "request_headers": _loads(d.get("request_headers"), {}),
        "request_body": d.get("request_body") or None,
        "response_status": int(rs) if rs else None,
        "response_headers": _loads(d.get("response_headers"), {}),
        "response_body": d.get("response_body") or None,
        "response_time_ms": int(d.get("response_time_ms") or 0),
        "created_at": d.get("ts"),
    }


def _snap_row(names: list, row: tuple) -> Dict[str, Any]:
    d = dict(zip(names, row))
    return {
        "request_id": d.get("request_id") or None,
        "merchant_id": _as_int_or_none(d.get("merchant_id")),
        "order_id": _as_int_or_none(d.get("order_id")),
        "response_time_ms": int(d.get("response_time_ms") or 0),
        "request_data": _loads(d.get("request_data"), {}),
        "merchant_snapshot": _loads(d.get("merchant_snapshot"), {}),
        "rate_snapshot": _loads(d.get("rate_snapshot"), None),
        "traders_snapshot": _loads(d.get("traders_snapshot"), []),
        "candidates": _loads(d.get("candidates"), []),
        "result": _loads(d.get("result"), {}),
        "created_at": d.get("ts"),
    }


# ── audit page: list logs ───────────────────────────────────────────────────

_SORT_COLUMNS = {
    "created_at": "ts",
    "response_status": "response_status",
    "method": "method",
    "merchant_id": "merchant_id",
}


async def list_merchant_api_logs(
    *,
    skip: int = 0,
    limit: int = 50,
    merchant_id: Optional[int] = None,
    method: Optional[str] = None,
    endpoint_group: Optional[str] = None,
    sort_by: Optional[str] = None,
    sort_order: Optional[str] = "desc",
) -> List[Dict[str, Any]]:
    where: List[str] = []
    params: Dict[str, Any] = {}
    if merchant_id:
        where.append("merchant_id = {mid:Int64}")
        params["mid"] = int(merchant_id)
    if method:
        where.append("method = {method:String}")
        params["method"] = method.upper()
    if endpoint_group and endpoint_group != "all":
        if endpoint_group == "bot":
            where.append("url LIKE {gbot:String}")
            params["gbot"] = "/api/bot/v1/%"
        elif endpoint_group == "unknown":
            for i, pat in enumerate([
                "/api/merchant/v1/orders%", "/api/merchant/v1/payments%",
                "/api/merchant/v1/rates%", "/api/merchant/v1/profile%",
                "/api/merchant/v1/callbacks%", "/api/bot/v1/%",
            ]):
                key = f"nu{i}"
                where.append(f"url NOT LIKE {{{key}:String}}")
                params[key] = pat
        else:
            where.append("(url LIKE {gm:String} OR url LIKE {gb:String})")
            params["gm"] = f"/api/merchant/v1/{endpoint_group}%"
            params["gb"] = f"/api/bot/v1/merchants/%/{endpoint_group}%"

    col = _SORT_COLUMNS.get(sort_by or "", "ts")
    order = "ASC" if (sort_order == "asc") else "DESC"
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (
        f"SELECT {', '.join(LOG_COLUMNS)} FROM {_LOG_TABLE}{where_sql} "
        f"ORDER BY {col} {order} LIMIT {int(limit)} OFFSET {int(skip)}"
    )
    out = await _fetch(sql, params)
    if not out:
        return []
    names, rows = out
    return [_log_row(names, r) for r in rows]


async def get_snapshot_by_request_id(request_id: str) -> Optional[Dict[str, Any]]:
    sql = (
        f"SELECT {', '.join(SNAP_COLUMNS)} FROM {_SNAP_TABLE} "
        f"WHERE request_id = {{rid:String}} ORDER BY ts DESC LIMIT 1"
    )
    out = await _fetch(sql, {"rid": request_id})
    if not out:
        return None
    names, rows = out
    return _snap_row(names, rows[0]) if rows else None


# ── order-debug ─────────────────────────────────────────────────────────────


async def list_logs_for_order(order_id: int) -> List[Dict[str, Any]]:
    sql = (
        f"SELECT {', '.join(LOG_COLUMNS)} FROM {_LOG_TABLE} "
        f"WHERE order_id = {{oid:String}} ORDER BY ts ASC LIMIT 200"
    )
    out = await _fetch(sql, {"oid": str(order_id)})
    if not out:
        return []
    names, rows = out
    return [_log_row(names, r) for r in rows]


async def get_latest_snapshot_for_order(order_id: int) -> Optional[Dict[str, Any]]:
    sql = (
        f"SELECT {', '.join(SNAP_COLUMNS)} FROM {_SNAP_TABLE} "
        f"WHERE order_id = {{oid:String}} ORDER BY ts DESC LIMIT 1"
    )
    out = await _fetch(sql, {"oid": str(order_id)})
    if not out:
        return None
    names, rows = out
    return _snap_row(names, rows[0]) if rows else None


# ── statistics ──────────────────────────────────────────────────────────────


def _date_conds(
    where: List[str], params: Dict[str, Any], *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    since: Optional[datetime] = None,
) -> None:
    if date_from is not None:
        where.append("ts >= {df:DateTime64(3)}")
        params["df"] = _naive(date_from)
    if date_to is not None:
        where.append("ts <= {dt:DateTime64(3)}")
        params["dt"] = _naive(date_to)
    if since is not None:
        where.append("ts > {since:DateTime64(3)}")
        params["since"] = _naive(since)


async def count_payin_requests(
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    merchant_id: Optional[int] = None,
    since: Optional[datetime] = None,
) -> int:
    where = ["method = 'POST'", _PAYIN_CREATE]
    params: Dict[str, Any] = {}
    if merchant_id is not None:
        where.append("merchant_id = {mid:Int64}")
        params["mid"] = int(merchant_id)
    _date_conds(where, params, date_from=date_from, date_to=date_to, since=since)
    sql = f"SELECT count() FROM {_LOG_TABLE} WHERE " + " AND ".join(where)
    return int(await _scalar(sql, params, 0) or 0)


async def count_payin_requests_by_merchant(
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
) -> Dict[int, int]:
    """``{merchant_id: payin-create request count}`` (incl. non-created attempts)
    over the range, in ONE grouped CH query. Fail-safe ``{}`` on CH-off/error."""
    where = ["method = 'POST'", _PAYIN_CREATE]
    params: Dict[str, Any] = {}
    _date_conds(where, params, date_from=date_from, date_to=date_to)
    sql = (
        f"SELECT merchant_id AS m, count() AS c FROM {_LOG_TABLE} "
        f"WHERE {' AND '.join(where)} GROUP BY merchant_id"
    )
    out = await _fetch(sql, params)
    if not out:
        return {}
    names, rows = out
    mi, ci = names.index("m"), names.index("c")
    return {int(r[mi]): int(r[ci]) for r in rows if r[mi]}


async def payin_requests_timeseries_by_merchant(
    *,
    merchant_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    granularity: str = "hour",
) -> List[Dict[str, Any]]:
    """Per-bucket payin-create request counts for one merchant. Each dict:
    ``bucket`` (unix seconds), ``requests``. Fail-safe ``[]`` on CH-off/error."""
    bucket_expr = _BUCKET_EXPR.get(granularity, _BUCKET_EXPR["hour"])
    where = ["method = 'POST'", _PAYIN_CREATE, "merchant_id = {mid:Int64}"]
    params: Dict[str, Any] = {"mid": int(merchant_id)}
    _date_conds(where, params, date_from=date_from, date_to=date_to)
    sql = (
        f"SELECT toUnixTimestamp({bucket_expr}) AS bucket, count() AS c "
        f"FROM {_LOG_TABLE} WHERE {' AND '.join(where)} GROUP BY bucket ORDER BY bucket"
    )
    out = await _fetch(sql, params)
    if not out:
        return []
    names, rows = out
    bi, ci = names.index("bucket"), names.index("c")
    return [{"bucket": int(r[bi]), "requests": int(r[ci])} for r in rows]


async def sum_payin_amount(
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    merchant_id: Optional[int] = None,
    since: Optional[datetime] = None,
) -> float:
    where = ["method = 'POST'", _PAYIN_CREATE]
    params: Dict[str, Any] = {}
    if merchant_id is not None:
        where.append("merchant_id = {mid:Int64}")
        params["mid"] = int(merchant_id)
    _date_conds(where, params, date_from=date_from, date_to=date_to, since=since)
    sql = f"SELECT sum({_AMOUNT}) FROM {_LOG_TABLE} WHERE " + " AND ".join(where)
    return float(await _scalar(sql, params, 0.0) or 0.0)


async def failed_payin_amount(
    *,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    since: Optional[datetime] = None,
) -> float:
    where = [
        "method = 'POST'", "url LIKE '%/payin%'",
        "order_id = ''", "response_status >= 400",
    ]
    params: Dict[str, Any] = {}
    _date_conds(where, params, date_from=date_from, date_to=date_to, since=since)
    sql = f"SELECT sum({_AMOUNT}) FROM {_LOG_TABLE} WHERE " + " AND ".join(where)
    return float(await _scalar(sql, params, 0.0) or 0.0)


async def volume_distribution_24h() -> List[Dict[str, Any]]:
    sql = (
        f"SELECT {_METHOD} AS payment_method, "
        f"sumIf({_AMOUNT}, {_AMOUNT} < 1000) AS lt_1000, "
        f"sumIf({_AMOUNT}, {_AMOUNT} >= 1000 AND {_AMOUNT} < 5000) AS from_1000, "
        f"sumIf({_AMOUNT}, {_AMOUNT} >= 5000 AND {_AMOUNT} < 8000) AS from_5000, "
        f"sumIf({_AMOUNT}, {_AMOUNT} >= 8000 AND {_AMOUNT} < 10000) AS from_8000, "
        f"sumIf({_AMOUNT}, {_AMOUNT} >= 10000 AND {_AMOUNT} < 20000) AS from_10000, "
        f"sumIf({_AMOUNT}, {_AMOUNT} >= 20000) AS from_20000 "
        f"FROM {_LOG_TABLE} "
        f"WHERE method = 'POST' AND {_PAYIN_CREATE} "
        f"AND ts >= now() - INTERVAL 24 HOUR AND JSONHas(request_body, 'amount') "
        f"GROUP BY payment_method"
    )
    out = await _fetch(sql, {})
    if not out:
        return []
    names, rows = out
    items: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(zip(names, r))
        items.append({
            "method": d.get("payment_method") or "unknown",
            "lt_1000": float(d.get("lt_1000") or 0),
            "from_1000": float(d.get("from_1000") or 0),
            "from_5000": float(d.get("from_5000") or 0),
            "from_8000": float(d.get("from_8000") or 0),
            "from_10000": float(d.get("from_10000") or 0),
            "from_20000": float(d.get("from_20000") or 0),
        })
    return items


async def list_order_creation_requests(
    *, skip: int = 0, limit: int = 50,
) -> Tuple[List[Dict[str, Any]], int]:
    """Recent order-creation snapshots (request_id / merchant_id / request_data /
    result / response_time_ms / created_at), newest first, + total count.
    ``merchant_login`` is resolved from Postgres by the caller."""
    total = int(await _scalar(f"SELECT count() FROM {_SNAP_TABLE}", {}, 0) or 0)
    if not total:
        return [], 0
    sql = (
        "SELECT request_id, merchant_id, response_time_ms, request_data, result, ts "
        f"FROM {_SNAP_TABLE} ORDER BY ts DESC LIMIT {int(limit)} OFFSET {int(skip)}"
    )
    out = await _fetch(sql, {})
    if not out:
        return [], total
    names, rows = out
    items: List[Dict[str, Any]] = []
    for r in rows:
        d = dict(zip(names, r))
        items.append({
            "request_id": d.get("request_id") or None,
            "merchant_id": _as_int_or_none(d.get("merchant_id")),
            "response_time_ms": int(d.get("response_time_ms") or 0),
            "request_data": _loads(d.get("request_data"), {}),
            "result": _loads(d.get("result"), {}),
            "created_at": d.get("ts"),
        })
    return items, total
