import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Mapping, Optional

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.common.enums.cascading import CascadeAttemptStatus
from app.common.types import utcnow
from app.core.config import get_settings
from app.core.context import provider_requests_var
from app.infrastructure.clickhouse import client as ch
from app.infrastructure.clickhouse.sink import MAX_BODY, MAX_ERROR, emit_record
from app.modules.base.repository import BaseRepository
from app.modules.cascading.models import (
    CascadeGroup,
    CascadeOrderAttempt,
    CascadeProvider,
    CascadeProviderMetric,
    ProviderCallbackAttempt,
    cascade_group_merchants,
)


class CascadeProviderRepository(BaseRepository[CascadeProvider]):
    def __init__(self, session: AsyncSession):
        super().__init__(CascadeProvider, session)

    async def list_all(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        is_active: Optional[bool] = None,
        search: Optional[str] = None,
    ) -> List[CascadeProvider]:
        stmt = select(self.model)
        if is_active is not None:
            stmt = stmt.where(self.model.is_active == is_active)
        if search:
            like = f"%{search}%"
            stmt = stmt.where(
                or_(self.model.code.ilike(like), self.model.name.ilike(like))
            )
        stmt = stmt.order_by(self.model.id.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all())

    async def get_by_code(self, code: str) -> Optional[CascadeProvider]:
        result = await self.session.execute(
            select(self.model).where(self.model.code == code)
        )
        return result.scalars().first()

    async def list_active(self) -> List[CascadeProvider]:
        result = await self.session.execute(
            select(self.model)
            .where(self.model.is_active.is_(True))
            .order_by(self.model.priority_weight.desc(), self.model.id.asc())
        )
        return list(result.scalars().unique().all())


class CascadeGroupRepository(BaseRepository[CascadeGroup]):
    def __init__(self, session: AsyncSession):
        super().__init__(CascadeGroup, session)

    async def list_all(self, *, is_active: Optional[bool] = None) -> List[CascadeGroup]:
        stmt = select(self.model).options(
            selectinload(self.model.providers),
            selectinload(self.model.merchants),
        )
        if is_active is not None:
            stmt = stmt.where(self.model.is_active == is_active)
        stmt = stmt.order_by(self.model.tier.asc(), self.model.id.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all())

    async def list_for_merchant(self, merchant_id: int) -> List[CascadeGroup]:
        """Return groups bound to the merchant, ordered by tier asc.

        Eager-loads providers so the cascade race can iterate without N+1.
        """
        stmt = (
            select(self.model)
            .join(cascade_group_merchants, cascade_group_merchants.c.group_id == self.model.id)
            .where(
                cascade_group_merchants.c.merchant_id == merchant_id,
                self.model.is_active.is_(True),
            )
            .options(selectinload(self.model.providers))
            .order_by(self.model.tier.asc(), self.model.id.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all())

    async def get_with_relations(self, group_id: int) -> Optional[CascadeGroup]:
        stmt = (
            select(self.model)
            .where(self.model.id == group_id)
            .options(
                selectinload(self.model.providers),
                selectinload(self.model.merchants),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()


class CascadeOrderAttemptRepository(BaseRepository[CascadeOrderAttempt]):
    def __init__(self, session: AsyncSession):
        super().__init__(CascadeOrderAttempt, session)

    async def list_for_order(self, order_id: int) -> List[CascadeOrderAttempt]:
        result = await self.session.execute(
            select(self.model)
            .where(self.model.order_id == order_id)
            .order_by(self.model.started_at.asc())
        )
        return list(result.scalars().all())

    async def get_won_for_order(self, order_id: int) -> Optional[CascadeOrderAttempt]:
        result = await self.session.execute(
            select(self.model).where(
                self.model.order_id == order_id,
                self.model.status == CascadeAttemptStatus.WON,
            )
        )
        return result.scalars().first()

    async def get_by_external(
        self, provider_id: int, external_order_id: str
    ) -> Optional[CascadeOrderAttempt]:
        result = await self.session.execute(
            select(self.model).where(
                self.model.provider_id == provider_id,
                self.model.external_order_id == external_order_id,
            )
        )
        return result.scalars().first()

    async def list_in_flight(self, *, older_than_seconds: int = 0) -> List[CascadeOrderAttempt]:
        threshold = utcnow() - timedelta(seconds=older_than_seconds)
        result = await self.session.execute(
            select(self.model).where(
                self.model.status == CascadeAttemptStatus.IN_FLIGHT,
                self.model.started_at <= threshold,
            )
        )
        return list(result.scalars().all())

    async def aggregate_for_provider(
        self, provider_id: int, since: datetime
    ) -> dict:
        """Roll up recent attempts for a provider into the metric counters.

        Used by the hourly aggregator and the POOLED scoring helper.
        """
        result = await self.session.execute(
            select(
                func.count(self.model.id).label("requests"),
                func.sum(
                    case((self.model.status == CascadeAttemptStatus.WON, 1), else_=0)
                ).label("wins"),
                func.sum(
                    case((self.model.status == CascadeAttemptStatus.ERROR, 1), else_=0)
                ).label("errors"),
                func.sum(
                    case((self.model.status == CascadeAttemptStatus.TIMEOUT, 1), else_=0)
                ).label("timeouts"),
                func.sum(
                    case((self.model.status == CascadeAttemptStatus.CANCELLED, 1), else_=0)
                ).label("cancels"),
                func.coalesce(func.avg(self.model.latency_ms), 0).label("avg_latency"),
            ).where(
                self.model.provider_id == provider_id,
                self.model.started_at >= since,
            )
        )
        row = result.one()
        requests = int(row.requests or 0)
        wins = int(row.wins or 0)
        errors = int(row.errors or 0)
        timeouts = int(row.timeouts or 0)
        cancels = int(row.cancels or 0)
        return {
            "requests": requests,
            "wins": wins,
            "errors": errors,
            "timeouts": timeouts,
            "cancels": cancels,
            "success_rate": (wins / requests) if requests else 0.0,
            "avg_latency_ms": float(row.avg_latency or 0),
        }


class CascadeProviderMetricRepository(BaseRepository[CascadeProviderMetric]):
    def __init__(self, session: AsyncSession):
        super().__init__(CascadeProviderMetric, session)

    async def list_for_provider(
        self,
        provider_id: int,
        *,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> List[CascadeProviderMetric]:
        stmt = select(self.model).where(self.model.provider_id == provider_id)
        if date_from:
            stmt = stmt.where(self.model.bucket_at >= date_from)
        if date_to:
            stmt = stmt.where(self.model.bucket_at <= date_to)
        stmt = stmt.order_by(self.model.bucket_at.asc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def upsert_bucket(
        self, provider_id: int, bucket_at: datetime, **counters
    ) -> CascadeProviderMetric:
        existing = await self.session.execute(
            select(self.model).where(
                self.model.provider_id == provider_id,
                self.model.bucket_at == bucket_at,
            )
        )
        row = existing.scalars().first()
        if row:
            for key, value in counters.items():
                setattr(row, key, value)
            self.session.add(row)
            await self.session.flush()
            return row
        return await self.create({"provider_id": provider_id, "bucket_at": bucket_at, **counters})


class ProviderCallbackAttemptRepository(BaseRepository[ProviderCallbackAttempt]):
    """Read access for the admin Callbacks page (``Провайдеры`` tab).

    Writes are done directly from the inbound webhook endpoint in an
    isolated session so the log row survives main-transaction rollbacks
    (see ``app/api/cascade/v1/endpoints/callbacks.py``).
    """

    def __init__(self, session: AsyncSession):
        super().__init__(ProviderCallbackAttempt, session)

    async def list_all(
        self,
        *,
        provider_id: Optional[int] = None,
        provider_code: Optional[str] = None,
        order_id: Optional[int] = None,
        external_order_id: Optional[str] = None,
        signature_valid: Optional[bool] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List[ProviderCallbackAttempt]:
        stmt = select(self.model)
        if provider_id is not None:
            stmt = stmt.where(self.model.provider_id == provider_id)
        if provider_code:
            stmt = stmt.where(self.model.provider_code == provider_code)
        if order_id is not None:
            stmt = stmt.where(self.model.order_id == order_id)
        if external_order_id:
            stmt = stmt.where(self.model.external_order_id == external_order_id)
        if signature_valid is not None:
            stmt = stmt.where(self.model.signature_valid.is_(signature_valid))
        stmt = stmt.order_by(self.model.created_at.desc()).offset(skip).limit(limit)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


# ─────────────────────────────────────────────────────────────────────────────
# ClickHouse: outbound provider HTTP requests + inbound provider callbacks
# ─────────────────────────────────────────────────────────────────────────────
# Records are delivered via the shared batched sink
# (app.infrastructure.clickhouse.sink); DDL lives in
# app/infrastructure/clickhouse/schema/cascading.sql. Fail-safe everywhere —
# logging a provider exchange must never break or slow a call. Reads return []
# when CH is disabled or on error so admin pages stay usable.

logger = logging.getLogger("cascading.repository")

_PROVIDER_REQUESTS_TABLE = "provider_requests"
_PROVIDER_CALLBACKS_TABLE = "provider_callbacks"

# Column order — must match the INSERT and the DDL in schema/cascading.sql.
COLUMNS = [
    "ts", "request_id", "provider_id", "provider_code", "order_id",
    "method", "url", "request_headers", "request_body",
    "response_status", "response_body", "success", "error",
    "provider_latency_ms", "e2e_ms", "request_type",
]

CALLBACK_COLUMNS = [
    "ts", "request_id", "provider_id", "provider_code",
    "order_id", "external_order_id", "signature_valid",
    "parsed_status", "request_headers", "request_body",
    "response_status", "response_body", "error", "processing_ms",
]

# Credential-bearing headers we must never store raw (mirrors the set used by
# the admin request-preview in cascading/service.py).
_SENSITIVE_HEADER_NAMES = {
    "authorization", "x-api-key", "x-secret", "x-secret-phrase", "x-signature",
    "x-identity", "x-notification-token", "x-mock-signature", "x-hash",
}


def mask_sensitive_headers(headers: Mapping[str, str]) -> Dict[str, str]:
    """Copy of ``headers`` with credential values masked (prefix + length kept
    so operators can sanity-check signing produced something non-empty)."""
    masked: Dict[str, str] = {}
    for k, v in (headers or {}).items():
        if k.lower() in _SENSITIVE_HEADER_NAMES and v:
            masked[k] = f"{v[:6]}… ({len(v)} chars)"
        else:
            masked[k] = v
    return masked


@dataclass
class ProviderRequestRecord:
    provider_id: str
    provider_code: str
    method: str
    url: str
    request_headers: str          # JSON (masked)
    request_body: str
    response_status: int          # 0 on network error / timeout
    response_body: str
    success: bool
    error: str
    provider_latency_ms: int
    ts: datetime = field(default_factory=utcnow)
    request_id: str = ""
    order_id: str = ""
    e2e_ms: int = 0
    request_type: str = "other"   # payin / cancel / balance / check / upload / …

    def as_row(self) -> list:
        return [
            self.ts, self.request_id, self.provider_id, self.provider_code,
            self.order_id, self.method, self.url, self.request_headers,
            self.request_body, int(self.response_status), self.response_body,
            1 if self.success else 0, self.error, int(self.provider_latency_ms),
            int(self.e2e_ms), str(self.request_type or "other"),
        ]


@dataclass
class ProviderCallbackRecord:
    provider_id: str
    provider_code: str
    order_id: str
    external_order_id: str
    signature_valid: bool
    parsed_status: str
    request_headers: str          # JSON (masked)
    request_body: str             # raw provider payload
    response_status: int          # the HTTP status we returned
    response_body: str            # the body we returned
    error: str
    processing_ms: int
    ts: datetime = field(default_factory=utcnow)
    request_id: str = ""

    def as_row(self) -> list:
        return [
            self.ts, self.request_id, self.provider_id, self.provider_code,
            self.order_id, self.external_order_id, 1 if self.signature_valid else 0,
            self.parsed_status, self.request_headers, self.request_body,
            int(self.response_status), self.response_body, self.error,
            int(self.processing_ms),
        ]


ProviderRequestRecord.table = _PROVIDER_REQUESTS_TABLE
ProviderRequestRecord.columns = COLUMNS
ProviderCallbackRecord.table = _PROVIDER_CALLBACKS_TABLE
ProviderCallbackRecord.columns = CALLBACK_COLUMNS


def _int_or_none(v: Any) -> Optional[int]:
    s = str(v if v is not None else "")
    return int(s) if s.isdigit() else None


def record_provider_request(
    *,
    provider: Any,
    method: str,
    url: str,
    headers: Optional[Mapping[str, str]],
    body: str,
    status: int,
    response_body: str,
    success: bool,
    error: str,
    provider_latency_ms: int,
    request_type: str = "other",
) -> None:
    """Build a provider-request record at the chokepoint and route it.

    Inside a merchant request → append to ``provider_requests_var`` (the
    middleware stamps e2e_ms / request_id / order_id and emits at request end).
    Otherwise → emit now (e2e_ms stays 0). Best-effort; never raises.
    """
    try:
        rec = ProviderRequestRecord(
            provider_id=str(getattr(provider, "id", "") or ""),
            provider_code=str(getattr(provider, "code", "") or ""),
            method=method,
            url=url,
            request_headers=json.dumps(mask_sensitive_headers(headers or {}), default=str)[:MAX_BODY],
            request_body=(body or "")[:MAX_BODY],
            response_status=int(status or 0),
            response_body=(response_body or "")[:MAX_BODY],
            success=bool(success),
            error=(error or "")[:MAX_ERROR],
            provider_latency_ms=int(provider_latency_ms),
            request_type=str(request_type or "other"),
        )
        buf = provider_requests_var.get()
        if isinstance(buf, list):
            buf.append(rec)
        else:
            emit_record(rec)
    except Exception as exc:  # noqa: BLE001 — logging must never break a provider call
        logger.warning("record_provider_request failed: %s", exc)


def record_provider_callback(
    *,
    provider_code: str,
    provider_id: Any,
    headers: Optional[Mapping[str, str]],
    body: str,
    signature_valid: bool,
    parsed_status: Optional[str],
    external_order_id: Optional[str],
    order_id: Any,
    response_status: int,
    response_body: str,
    error: Optional[str],
    processing_ms: int,
) -> None:
    """Build a record for one inbound provider callback and emit it.

    No request buffer: the public callback endpoint is not wrapped by the
    merchant-API middleware, so we emit straight to the batched sink (web
    process). Headers are masked. Best-effort; never raises.
    """
    try:
        rid = ""
        try:
            from app.core.middleware.request_id import request_id_context_var
            rid = request_id_context_var.get() or ""
        except Exception:  # noqa: BLE001 — correlation id is optional
            rid = ""
        rec = ProviderCallbackRecord(
            provider_id=str(provider_id or ""),
            provider_code=str(provider_code or ""),
            order_id=str(order_id or ""),
            external_order_id=str(external_order_id or ""),
            signature_valid=bool(signature_valid),
            parsed_status=str(parsed_status or ""),
            request_headers=json.dumps(mask_sensitive_headers(headers or {}), default=str)[:MAX_BODY],
            request_body=(body or "")[:MAX_BODY],
            response_status=int(response_status or 0),
            response_body=(response_body or "")[:MAX_BODY],
            error=(error or "")[:MAX_ERROR],
            processing_ms=int(processing_ms or 0),
            request_id=rid,
        )
        emit_record(rec)
    except Exception as exc:  # noqa: BLE001 — logging must never break a callback
        logger.warning("record_provider_callback failed: %s", exc)


async def query_provider_requests(
    *,
    limit: int = 25,
    offset: int = 0,
    provider_code: Optional[str] = None,
    success: Optional[bool] = None,
    order_id: Optional[str] = None,
    request_id: Optional[str] = None,
    request_type: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Read recent provider request logs from ClickHouse, newest first."""
    if not get_settings().CLICKHOUSE_ENABLED:
        return []

    where: List[str] = []
    params: Dict[str, Any] = {}
    if provider_code:
        where.append("provider_code = {pc:String}")
        params["pc"] = provider_code
    if request_type:
        where.append("request_type = {rt:String}")
        params["rt"] = request_type
    if success is not None:
        where.append("success = {s:UInt8}")
        params["s"] = 1 if success else 0
    if order_id:
        where.append("order_id = {oid:String}")
        params["oid"] = order_id
    if request_id:
        where.append("request_id = {rid:String}")
        params["rid"] = request_id

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (
        f"SELECT {', '.join(COLUMNS)} FROM {_PROVIDER_REQUESTS_TABLE}{where_sql} "
        f"ORDER BY ts DESC LIMIT {int(limit)} OFFSET {int(offset)}"
    )

    def _run() -> List[Dict[str, Any]]:
        res = ch.get_client().query(sql, parameters=params)
        names = res.column_names
        return [dict(zip(names, row)) for row in res.result_rows]

    try:
        return await asyncio.get_running_loop().run_in_executor(None, _run)
    except Exception as exc:  # noqa: BLE001 — read failures must not 500 the page
        logger.warning("provider_requests query failed: %s", exc)
        return []


async def query_provider_callbacks(
    *,
    limit: int = 50,
    offset: int = 0,
    provider_code: Optional[str] = None,
    order_id: Optional[int] = None,
    external_order_id: Optional[str] = None,
    signature_valid: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """Read recent inbound provider callbacks from ClickHouse, newest first.

    Rows are shaped as the admin Callbacks page consumes them (``created_at`` /
    ``error_message`` / ``request_headers`` as an object).
    """
    if not get_settings().CLICKHOUSE_ENABLED:
        return []

    where: List[str] = []
    params: Dict[str, Any] = {}
    if provider_code:
        where.append("provider_code = {pc:String}")
        params["pc"] = provider_code
    if order_id is not None:
        where.append("order_id = {oid:String}")
        params["oid"] = str(order_id)
    if external_order_id:
        where.append("external_order_id = {eoid:String}")
        params["eoid"] = external_order_id
    if signature_valid is not None:
        where.append("signature_valid = {sv:UInt8}")
        params["sv"] = 1 if signature_valid else 0

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    sql = (
        f"SELECT {', '.join(CALLBACK_COLUMNS)} FROM {_PROVIDER_CALLBACKS_TABLE}{where_sql} "
        f"ORDER BY ts DESC LIMIT {int(limit)} OFFSET {int(offset)}"
    )

    def _run() -> List[Dict[str, Any]]:
        res = ch.get_client().query(sql, parameters=params)
        names = res.column_names
        out: List[Dict[str, Any]] = []
        for row in res.result_rows:
            d = dict(zip(names, row))
            raw_headers = d.get("request_headers") or "{}"
            try:
                headers = json.loads(raw_headers)
                if not isinstance(headers, dict):
                    headers = {"_raw": raw_headers}
            except Exception:  # noqa: BLE001 — tolerate non-JSON headers
                headers = {"_raw": raw_headers}
            out.append({
                "provider_id": _int_or_none(d.get("provider_id")),
                "provider_code": d.get("provider_code") or "",
                "order_id": _int_or_none(d.get("order_id")),
                "external_order_id": d.get("external_order_id") or None,
                "signature_valid": bool(d.get("signature_valid")),
                "parsed_status": d.get("parsed_status") or None,
                "request_headers": headers,
                "request_body": d.get("request_body") or None,
                "response_status": int(d["response_status"]) if d.get("response_status") else None,
                "response_body": d.get("response_body") or None,
                "error_message": d.get("error") or None,
                "processing_ms": int(d.get("processing_ms") or 0),
                "request_id": d.get("request_id") or None,
                "created_at": d.get("ts"),
            })
        return out

    try:
        return await asyncio.get_running_loop().run_in_executor(None, _run)
    except Exception as exc:  # noqa: BLE001 — read failures must not 500 the page
        logger.warning("provider_callbacks query failed: %s", exc)
        return []
