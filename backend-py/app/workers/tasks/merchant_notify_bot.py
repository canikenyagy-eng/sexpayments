"""Worker tasks for merchant-notify-bot — push notifications to a
merchant's Telegram group when something needs their attention.

Currently exposes one task:
  * ``notify_merchant_premoderation_request`` — sends "Прикрепите PDF" /
    "Прикрепите видео" to the merchant's configured notify group when an
    admin rejects a receipt in support-bot.

Graceful degradation:
  * MERCHANT_NOTIFY_BOT_URL/SECRET unset → warning, no-op
  * Merchant has no ``notify_telegram_group_id`` → warning, no-op
    (the rejection is still visible via ``orders.moderation_status``,
    so API/UI merchants can see it without bot push)
"""
import asyncio
from typing import Any, Dict

import httpx

from app.common.enums.receipt_moderations import ModerationDecision
from app.core.config import get_settings
from app.core.logging import get_logger
from app.infrastructure.db.session import WorkerSessionLocal as SessionLocal
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


# Decision → human-readable instruction the bot will post. Kept here (not
# in the bot) so the wording stays under version control with the backend
# logic that decides which decision was made.
# Base text (no External ID). The merchant-bot push (bot-created orders) uses it
# as-is — the order's external_id there is the internal ``bot_{chat}_{hex}``
# encoding, meaningless to the merchant. The merchant-notify-bot branch (API/web
# orders) appends the real External ID below.
_DECISION_TEXTS: Dict[str, str] = {
    ModerationDecision.REQUEST_PDF.value: (
        "📄 По ордеру требуется чек в формате PDF.\n\n"
        "UUID: <code>{order_uuid}</code>"
    ),
    ModerationDecision.REQUEST_VIDEO.value: (
        "🎥 По ордеру требуется видео-подтверждение оплаты.\n\n"
        "UUID: <code>{order_uuid}</code>"
    ),
}


def _chat_id_from_external_id(external_id) -> int | None:
    """merchant-bot encodes the Telegram chat id in the order's external_id as
    ``bot_{chat_id}_{hex}`` (see merchant-bot/bot/utils/tracker.py). Extract it so
    a proof request for a bot-created order can be pushed back to that chat."""
    if not external_id or not str(external_id).startswith("bot_"):
        return None
    try:
        return int(str(external_id).split("_", 2)[1])
    except (IndexError, ValueError):
        return None


@celery_app.task(
    name="app.workers.tasks.merchant_notify_bot.notify_merchant_premoderation_request",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def notify_merchant_premoderation_request(self, order_id: int, decision: str):
    """Send the PDF/Video re-upload request to the merchant's notify group.

    ``decision`` is the raw value of ``ModerationDecision`` — kept as a
    string so the task is JSON-serialisable for Celery. ACCEPT is never
    routed here (only PDF/VIDEO rejections trigger this task).
    """
    settings = get_settings()

    text_template = _DECISION_TEXTS.get(decision)
    if text_template is None:
        logger.warning(
            "notify_merchant_premoderation_request skipped: unsupported decision",
            order_id=order_id,
            decision=decision,
        )
        return

    async def _run() -> str:
        async with SessionLocal() as session:
            from app.common.enums.orders import OrderSource
            from app.modules.orders.models import Order
            from app.modules.merchants.models import Merchant

            order = await session.get(Order, order_id)
            if not order:
                return "order_not_found"

            merchant = await session.get(Merchant, order.merchant_id)
            if not merchant:
                return "merchant_not_found"

            # Per-merchant toggle: OFF → the API webhook carries the request; no
            # chat push at all (neither merchant-notify-bot nor merchant-bot).
            if not getattr(merchant, "proof_request_notify_enabled", True):
                return "proof_notify_disabled"

            text = text_template.format(order_uuid=str(order.uuid))

            # BOT-created order → push to merchant-bot with an "attach proof"
            # button (the merchant uploads the video/PDF right in the bot, the same
            # way they confirm a bot order).
            if order.source == OrderSource.BOT:
                chat_id = _chat_id_from_external_id(order.external_id)
                if chat_id is None:
                    return "bot_chat_missing"
                if not settings.MERCHANT_BOT_URL or not settings.MERCHANT_BOT_SECRET:
                    return "merchant_bot_unconfigured"
                payload: Dict[str, Any] = {
                    "chat_id": chat_id,
                    "text": text,
                    "order_uuid": str(order.uuid),
                    "external_id": order.external_id,
                    "decision": decision,
                    "merchant_id": order.merchant_id,
                }
                async with httpx.AsyncClient(timeout=10) as client:
                    resp = await client.post(
                        f"{settings.MERCHANT_BOT_URL.rstrip('/')}/proof_requested",
                        json=payload,
                        headers={"X-Bot-Secret": settings.MERCHANT_BOT_SECRET},
                    )
                    resp.raise_for_status()
                return "sent_merchant_bot"

            # API/web order → text notification to the merchant's notify group.
            if not settings.MERCHANT_NOTIFY_BOT_URL or not settings.MERCHANT_NOTIFY_BOT_SECRET:
                return "notify_bot_unconfigured"
            group_id = getattr(merchant, "notify_telegram_group_id", None)
            if not group_id:
                return "notify_group_missing"
            payload = {
                "chat_id": int(group_id),
                "text": f"{text}\nExternal ID: <code>{order.external_id or '—'}</code>",
                "order_uuid": str(order.uuid),
                "external_id": order.external_id,
                "decision": decision,
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{settings.MERCHANT_NOTIFY_BOT_URL.rstrip('/')}/moderation_request",
                    json=payload,
                    headers={"X-Bot-Secret": settings.MERCHANT_NOTIFY_BOT_SECRET},
                )
                resp.raise_for_status()
            return "sent"

    try:
        result = asyncio.run(_run())
    except Exception as exc:
        logger.warning(
            "notify_merchant_premoderation_request failed",
            order_id=order_id,
            decision=decision,
            error=str(exc),
            attempt=self.request.retries + 1,
        )
        raise self.retry(exc=exc)

    if result in ("sent", "sent_merchant_bot"):
        logger.info(
            "notify_merchant_premoderation_request sent",
            order_id=order_id,
            decision=decision,
        )
        return

    logger.warning(
        "notify_merchant_premoderation_request skipped",
        order_id=order_id,
        decision=decision,
        reason=result,
    )
