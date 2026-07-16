"""ClickHouse access for the requisite-activity snapshot (admin «Активность» tab).

Write path: the per-minute ``snapshot_requisite_activity_task`` builds one
``RequisiteActivitySnapshotRecord`` per ready requisite and delivers the whole
tick as a single batched insert via ``sink.emit_records`` (never per-row).

Read path: ``query_activity`` groups the per-minute rows down to
per-requisite-per-bucket in ClickHouse (``GROUP BY bucket, requisite_id`` with
``min/max``) so the service only merges a handful of intervals per bucket in
Python. Reads are fail-safe (``[]`` when CH is disabled or on error) and fully
server-bound (``{name:Type}`` params); the only inline SQL is the constant
bucket expression, chosen from a fixed whitelist — never user input.

DDL: ``app/infrastructure/clickhouse/schema/activity.sql``.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from app.common.types import utcnow
from app.core.config import get_settings
from app.infrastructure.clickhouse import client as ch
from app.infrastructure.clickhouse.sink import emit_records

logger = logging.getLogger("stats.activity_ch")

_TABLE = "requisite_activity_snapshots"

COLUMNS = [
    "ts", "requisite_id", "trader_id", "currency",
    "limit_min", "limit_max", "limit_total",
]

# Granularity → constant ClickHouse bucket-start expression. Whitelisted: the
# service only ever passes one of these keys, so the value is never user input
# spliced into SQL. The ``ts`` column is DateTime64(3, 'UTC'), so these truncation
# funcs + toUnixTimestamp bucket in UTC regardless of the CH server TZ, matching
# the service's aware-UTC backfill walker (ISO-Monday weeks, calendar months).
_BUCKET_EXPR = {
    "minute": "toStartOfMinute(ts)",
    "hour": "toStartOfHour(ts)",
    "3hour": "toStartOfInterval(ts, INTERVAL 3 HOUR)",
    "day": "toStartOfDay(ts)",
    "week": "toMonday(ts)",
    "month": "toStartOfMonth(ts)",
}


# ── record (write path) ──────────────────────────────────────────────────────


@dataclass
class RequisiteActivitySnapshotRecord:
    requisite_id: int
    trader_id: int
    currency: str
    limit_min: Decimal
    limit_max: Decimal
    limit_total: Decimal
    ts: datetime = field(default_factory=utcnow)

    def as_row(self) -> list:
        return [
            self.ts, int(self.requisite_id or 0), int(self.trader_id or 0),
            self.currency, self.limit_min, self.limit_max, self.limit_total,
        ]


RequisiteActivitySnapshotRecord.table = _TABLE
RequisiteActivitySnapshotRecord.columns = COLUMNS


def insert_snapshots(records: List[RequisiteActivitySnapshotRecord]) -> None:
    """Persist one tick's worth of records in a single batched insert (fail-safe,
    no-op when CH is disabled)."""
    emit_records(records)


# ── read path ────────────────────────────────────────────────────────────────


def _naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo is not None else dt


async def _fetch(sql: str, params: Dict[str, Any]) -> Optional[List[dict]]:
    if not get_settings().CLICKHOUSE_ENABLED:
        return None

    def _run():
        # Serialize access to the non-thread-safe shared sync client.
        with ch.query_lock:
            res = ch.get_client().query(sql, parameters=params)
            return [dict(zip(res.column_names, row)) for row in res.result_rows]

    try:
        return await asyncio.get_running_loop().run_in_executor(None, _run)
    except Exception as exc:  # noqa: BLE001 — reads must not 500 the stats page
        logger.warning("%s query failed: %s", _TABLE, exc)
        return None


async def query_activity(
    date_from: datetime, date_to: datetime, currency: str, granularity: str,
) -> List[dict]:
    """Return per-requisite-per-bucket rows for the range/currency, already
    reduced in ClickHouse. Each dict: ``bucket`` (unix seconds), ``requisite_id``,
    ``trader_id``, ``mn`` (min limit), ``mx`` (max limit). Empty on CH-off/error."""
    bucket_expr = _BUCKET_EXPR.get(granularity, _BUCKET_EXPR["hour"])
    sql = (
        f"SELECT toUnixTimestamp({bucket_expr}) AS bucket, "
        "requisite_id, any(trader_id) AS trader_id, "
        "min(limit_min) AS mn, max(limit_max) AS mx "
        f"FROM {_TABLE} "
        "WHERE ts >= {df:DateTime64(3)} AND ts < {dt:DateTime64(3)} "
        "AND currency = {cur:String} "
        "GROUP BY bucket, requisite_id "
        "ORDER BY bucket"
    )
    params = {"df": _naive(date_from), "dt": _naive(date_to), "cur": currency}
    return await _fetch(sql, params) or []


async def available_currencies(date_from: datetime, date_to: datetime) -> List[str]:
    """Distinct currencies present in the range — feeds the tab's currency
    selector. Cheap (LowCardinality + partition scan). Empty on CH-off/error."""
    sql = (
        f"SELECT DISTINCT currency FROM {_TABLE} "
        "WHERE ts >= {df:DateTime64(3)} AND ts < {dt:DateTime64(3)} "
        "ORDER BY currency"
    )
    rows = await _fetch(sql, {"df": _naive(date_from), "dt": _naive(date_to)})
    return [r["currency"] for r in (rows or []) if r.get("currency")]
