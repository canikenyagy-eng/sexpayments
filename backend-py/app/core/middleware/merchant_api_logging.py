import logging
import time

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.context import payin_snapshot_var, provider_requests_var
from app.infrastructure.db.session import SessionLocal
from app.infrastructure.clickhouse.sink import emit_record
from app.modules.audit.repository import (
    record_merchant_api_log,
    record_order_creation_snapshot,
)

logger = logging.getLogger(__name__)

LOGGED_PREFIXES = ("/api/merchant/v1", "/api/bot/v1")
MAX_BODY_SIZE = 32_768


class MerchantApiLoggingMiddleware:
    """Pure-ASGI middleware for logging merchant API requests to ClickHouse.

    Avoids BaseHTTPMiddleware whose call_next runs in a child anyio TaskGroup,
    which prevents ContextVar changes (e.g. payin_snapshot_var) from
    propagating back to the parent task.

    The merchant_api_logs + order_creation_snapshots rows go to ClickHouse via
    the shared batched sink (re-keyed by request_id) — off the event loop, never
    blocking the response. Fully fail-safe.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        if not any(path.startswith(p) for p in LOGGED_PREFIXES):
            await self.app(scope, receive, send)
            return

        request_body_chunks: list[bytes] = []
        response_status = 500
        response_headers: dict[str, str] = {}
        response_body_chunks: list[bytes] = []

        async def receive_wrapper() -> dict:
            message = await receive()
            if message.get("type") == "http.request":
                request_body_chunks.append(message.get("body", b""))
            return message

        async def send_wrapper(message: dict) -> None:
            nonlocal response_status
            if message["type"] == "http.response.start":
                response_status = message["status"]
                for key, val in message.get("headers", []):
                    response_headers[key.decode("latin-1")] = val.decode("latin-1")
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    response_body_chunks.append(body)
            await send(message)

        safe_headers: dict[str, str] = {}
        for raw_hdr in scope.get("headers", []):
            name = raw_hdr[0].decode("latin-1").lower()
            if name not in ("authorization", "x-api-key", "x-api-secret", "x-signature", "x-bot-secret", "cookie"):
                safe_headers[name] = raw_hdr[1].decode("latin-1")

        provider_requests_var.set([])

        start_time = time.time()
        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        finally:
            elapsed_ms = int((time.time() - start_time) * 1000)

            request_body_raw = b"".join(request_body_chunks)
            request_body = (
                request_body_raw.decode("utf-8", errors="replace")[:MAX_BODY_SIZE]
                if request_body_raw else None
            )

            response_body_raw = b"".join(response_body_chunks)
            response_body = (
                response_body_raw.decode("utf-8", errors="replace")[:MAX_BODY_SIZE]
                if response_body_raw else None
            )

            state = scope.get("state", {})
            merchant_id = state.get("merchant_id") if isinstance(state, dict) else getattr(state, "merchant_id", None)
            request_id = (
                state.get("request_id") if isinstance(state, dict)
                else getattr(state, "request_id", None)
            )

            # Prefer the integer order_id stashed by the endpoint via
            # ``request.state.order_id`` — it's already known at the call
            # site and saves us a per-request lookup by UUID. The legacy
            # UUID-resolve fallback below is kept for endpoints that haven't
            # been updated yet (e.g. bot/admin paths).
            order_id = (
                state.get("order_id") if isinstance(state, dict)
                else getattr(state, "order_id", None)
            )
            if order_id is None and response_body:
                try:
                    import json
                    resp_json = json.loads(response_body)
                    if isinstance(resp_json, dict) and "id" in resp_json:
                        import uuid
                        try:
                            # Verify it's a valid UUID string
                            parsed_uuid = uuid.UUID(str(resp_json["id"]))

                            # Fetch the actual integer ID from the database using the UUID
                            async with SessionLocal() as session:
                                from sqlalchemy import select
                                from app.modules.orders.models import Order
                                stmt = select(Order.id).where(Order.uuid == parsed_uuid)
                                result = await session.execute(stmt)
                                order_id = result.scalar_one_or_none()
                        except ValueError:
                            pass
                except Exception:
                    pass

            # Flush buffered provider HTTP-request records, stamping e2e_ms /
            # request_id / order_id (these are deferred from request_signed).
            provider_records = provider_requests_var.get() or []
            provider_requests_var.set(None)
            for rec in provider_records:
                rec.e2e_ms = elapsed_ms
                if request_id:
                    rec.request_id = str(request_id)
                if order_id is not None:
                    rec.order_id = str(order_id)
                try:
                    emit_record(rec)
                except Exception:
                    pass

            snapshot_data = payin_snapshot_var.get()
            payin_snapshot_var.set(None)

            # Merchant API log + (optional) order-creation snapshot → ClickHouse.
            # Both are fail-safe (best-effort, batched off-loop); they never
            # raise into the request path.
            record_merchant_api_log(
                request_id=request_id,
                merchant_id=merchant_id,
                order_id=order_id,
                url=path,
                method=scope.get("method", ""),
                request_headers=safe_headers,
                request_body=request_body,
                response_status=response_status,
                response_headers=response_headers if response_headers else None,
                response_body=response_body,
                response_time_ms=elapsed_ms,
            )

            if snapshot_data is not None:
                record_order_creation_snapshot(
                    request_id=request_id,
                    merchant_id=merchant_id,
                    order_id=order_id,
                    response_time_ms=elapsed_ms,
                    snapshot=snapshot_data,
                )
