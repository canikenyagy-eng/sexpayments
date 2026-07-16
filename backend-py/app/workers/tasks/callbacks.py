import asyncio
import hashlib
import hmac
import json

from app.core.logging import get_logger
from app.infrastructure.db.session import WorkerSessionLocal as SessionLocal
from app.modules.callbacks.service import CallbackService
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(
    name="app.workers.tasks.callbacks.send_order_callback",
    bind=True,
    max_retries=5,
    default_retry_delay=60,  # 1 minute between retries
)
def send_order_callback(self, order_id: int):
    """
    Celery task to send a webhook callback for an order.
    It runs the async CallbackService method inside an event loop.
    Retries up to 5 times if the callback fails.
    """
    attempt_number = self.request.retries + 1
    
    logger.info(
        "Starting callback task",
        order_id=order_id,
        attempt=attempt_number,
    )

    async def _run():
        # WorkerSessionLocal has autocommit=False. The service commits itself
        # after writing the attempt row; we only roll back on unexpected errors
        # so connections aren't returned to the pool in a broken state.
        async with SessionLocal() as session:
            try:
                service = CallbackService(session)
                await service.send_callback(order_id, attempt_number=attempt_number)
            except Exception:
                await session.rollback()
                raise

    try:
        # Run the async code synchronously in the worker process
        asyncio.run(_run())
    except Exception as exc:
        logger.warning(
            "Callback task failed, scheduling retry",
            order_id=order_id,
            error=str(exc),
            attempt=attempt_number,
        )
        # Exponential backoff could be implemented here by modifying the delay
        raise self.retry(exc=exc)


@celery_app.task(
    name="app.workers.tasks.callbacks.send_payout_callback",
    bind=True,
    max_retries=5,
    default_retry_delay=60,
)
def send_payout_callback(self, payout_id: int):
    """Deliver a merchant webhook for a payout state change. Self-contained
    (the order CallbackService is order-coupled): canonical-JSON body signed
    with the terminal's secret via ``X-Signature``, POSTed to the payout's
    ``webhook_url`` (else the terminal's). No-op when no URL is configured.
    """
    import httpx

    from app.core.security import decrypt_api_secret
    from app.modules.payouts.models import Payout, PayoutTerminal

    attempt = self.request.retries + 1

    async def _run() -> str:
        async with SessionLocal() as session:
            payout = await session.get(Payout, payout_id)
            if not payout:
                return "payout_not_found"
            terminal = await session.get(PayoutTerminal, payout.payout_terminal_id)
            target_url = payout.webhook_url or (terminal.webhook_url if terminal else None)
            if not target_url:
                return "no_webhook"

            payload = {
                "id": str(payout.uuid),
                "external_id": payout.external_id,
                "status": payout.status.value,
                "amount": float(payout.amount),
                "currency": payout.currency.value,
                "user_id": payout.client_user_id,
                "created_at": payout.created_at.isoformat() if payout.created_at else None,
                "expires_at": payout.expires_at.isoformat() if payout.expires_at else None,
                "completed_at": payout.completed_at.isoformat() if payout.completed_at else None,
                "canceled_at": payout.canceled_at.isoformat() if payout.canceled_at else None,
                "rejection_reason": payout.rejection_reason,
            }
            body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            if terminal and terminal.api_secret:
                secret = decrypt_api_secret(terminal.api_secret)
                headers["X-Signature"] = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()

            # SSRF guard: payout webhook URL is merchant-controlled. Block
            # non-http(s)/private/loopback/link-local/metadata targets and pin
            # DNS so the worker can't reach internal hosts. A blocked URL is a
            # permanent skip (not a 5x-retried failure).
            from app.core.ssrf import PublicOnlyTransport, SsrfError, assert_public_url
            try:
                assert_public_url(target_url)
            except SsrfError as ssrf_err:
                logger.warning("Payout callback URL blocked by SSRF guard",
                               payout_id=payout_id, url=target_url, reason=str(ssrf_err))
                return "blocked"

            try:
                async with httpx.AsyncClient(
                    timeout=10.0, follow_redirects=False, transport=PublicOnlyTransport()
                ) as client:
                    resp = await client.post(target_url, content=body, headers=headers)
                    resp.raise_for_status()
            except SsrfError as ssrf_err:
                # Connect-time DNS-rebind block from PublicOnlyTransport — permanent
                # skip (not a 5x-retried failure), same as the pre-check.
                logger.warning("Payout callback URL blocked by SSRF guard at connect time",
                               payout_id=payout_id, url=target_url, reason=str(ssrf_err))
                return "blocked"
            return "sent"

    try:
        result = asyncio.run(_run())
    except Exception as exc:
        logger.warning("Payout callback failed, scheduling retry",
                       payout_id=payout_id, error=str(exc), attempt=attempt)
        raise self.retry(exc=exc)
    if result != "sent":
        logger.info("Payout callback skipped", payout_id=payout_id, reason=result)
