"""Public webhook endpoint for external cascade providers.

No authentication on the framework level — each adapter validates the
provider's own signature scheme (HMAC, JWT, mTLS, etc.) inside parse_callback.
A failed signature raises ``UnauthorizedException`` which the global error
handler turns into 401.

Every inbound POST is logged to ClickHouse (``provider_callbacks``) regardless
of outcome (success / bad signature / unknown order / parse error) so the admin
Callbacks page can surface the raw provider traffic for debugging. The log emit
is fully fail-safe (best-effort, batched off the event loop) and never affects
the callback response or its timing.
"""
import json
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_service
from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.modules.cascading.repository import record_provider_callback
from app.modules.cascading.service import CascadingService

logger = get_logger(__name__)

router = APIRouter()


def _decode_body_for_storage(raw: bytes) -> Optional[str]:
    """Best-effort UTF-8 decode for the stored ``request_body`` text. Binary
    garbage is dropped — we only care about the JSON payload providers send."""
    if not raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _log_provider_callback(
    *,
    provider_code: str,
    provider_id: Optional[int],
    request_headers: dict,
    request_body_bytes: bytes,
    signature_valid: bool,
    parsed_status: Optional[str],
    external_order_id: Optional[str],
    order_id: Optional[int],
    response_status: int,
    response_body: Any,
    error_message: Optional[str],
    processing_ms: int,
) -> None:
    """Emit the inbound callback to ClickHouse. Fail-safe; never raises."""
    body_text = (
        json.dumps(response_body, ensure_ascii=False)
        if isinstance(response_body, (dict, list))
        else (response_body or "")
    )
    record_provider_callback(
        provider_code=provider_code,
        provider_id=provider_id,
        headers=request_headers,
        body=_decode_body_for_storage(request_body_bytes) or "",
        signature_valid=signature_valid,
        parsed_status=parsed_status,
        external_order_id=external_order_id,
        order_id=order_id,
        response_status=response_status,
        response_body=body_text,
        error=error_message,
        processing_ms=processing_ms,
    )


@router.post(
    "/{provider_code}",
    summary="Receive a status callback from an external cascade provider",
)
async def cascade_callback(
    provider_code: str,
    request: Request,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    t0 = time.perf_counter()
    body = await request.body()
    headers = {k: v for k, v in request.headers.items()}

    provider = None
    parsed = None
    order = None

    def _ms() -> int:
        return round((time.perf_counter() - t0) * 1000)

    try:
        provider, parsed = await service.parse_provider_callback(
            provider_code=provider_code,
            headers=headers,
            body=body,
        )

        logger.info(
            "cascade_callback_received",
            provider_id=provider.id,
            provider_code=provider.code,
            external_order_id=parsed.external_order_id,
            provider_status=parsed.status.value,
        )

        order = await service.apply_callback(provider=provider, parsed=parsed)
        response = {
            "ok": True,
            "order_id": order.id if order else None,
            "external_order_id": parsed.external_order_id,
        }
    except AppException as exc:
        _log_provider_callback(
            provider_code=provider_code,
            provider_id=provider.id if provider else None,
            request_headers=headers,
            request_body_bytes=body,
            signature_valid=parsed is not None,
            parsed_status=parsed.status.value if parsed else None,
            external_order_id=parsed.external_order_id if parsed else None,
            order_id=None,
            response_status=exc.status_code,
            response_body={
                "ok": False,
                "error": {"code": exc.code, "message": exc.message},
            },
            error_message=exc.message,
            processing_ms=_ms(),
        )
        raise
    except Exception as exc:
        _log_provider_callback(
            provider_code=provider_code,
            provider_id=provider.id if provider else None,
            request_headers=headers,
            request_body_bytes=body,
            signature_valid=parsed is not None,
            parsed_status=parsed.status.value if parsed else None,
            external_order_id=parsed.external_order_id if parsed else None,
            order_id=None,
            response_status=500,
            response_body={
                "ok": False,
                "error": {"code": "internal_error", "message": str(exc)},
            },
            error_message=repr(exc),
            processing_ms=_ms(),
        )
        raise

    _log_provider_callback(
        provider_code=provider_code,
        provider_id=provider.id,
        request_headers=headers,
        request_body_bytes=body,
        signature_valid=True,
        parsed_status=parsed.status.value,
        external_order_id=parsed.external_order_id,
        order_id=order.id if order else None,
        response_status=200,
        response_body=response,
        error_message=None,
        processing_ms=_ms(),
    )
    return response
