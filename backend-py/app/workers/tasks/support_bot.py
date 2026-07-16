"""Worker tasks for the support-bot channel.

Currently exposes one task:
  * ``send_receipt_to_support_bot`` — delivers the receipt + inline
    keyboard to the support admin chat and back-fills the resulting
    Telegram message_id into the matching ``receipt_moderations`` row
    so a later callback handler can edit the keyboard.

Graceful degradation: when SUPPORT_BOT_URL / SUPPORT_BOT_SECRET are unset
the task logs a warning and exits cleanly — premoderation simply doesn't
deliver, but the order still has its PENDING moderation row and the admin
can manually approve via the admin UI.
"""
import asyncio
import json
from typing import Any, Dict, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.infrastructure.db.session import WorkerSessionLocal as SessionLocal
from app.workers.celery_app import celery_app

logger = get_logger(__name__)

_WITHDRAWAL_NOTIFY_KEY = "withdrawal:notify:{id}"
_WITHDRAWAL_NOTIFY_TTL = 30 * 24 * 3600  # 30 дней


async def _redis_set_json(key: str, value: Dict[str, Any], ttl: int) -> None:
    """Записать JSON в Redis свежим клиентом на ТЕКУЩЕМ event loop.

    Модульный ``redis_client`` из infrastructure.cache биндит пул к первому
    loop'у; Celery-таск создаёт новый loop через ``asyncio.run`` на каждый
    вызов, поэтому переиспользование общего клиента ловит "Future attached to
    a different loop". Короткоживущий клиент на вызов это исключает.
    """
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        str(get_settings().REDIS_URL), encoding="utf-8", decode_responses=True
    )
    try:
        await client.set(key, json.dumps(value), ex=ttl)
    finally:
        await client.aclose()


async def _redis_get_json(key: str) -> Optional[Dict[str, Any]]:
    import redis.asyncio as aioredis

    client = aioredis.from_url(
        str(get_settings().REDIS_URL), encoding="utf-8", decode_responses=True
    )
    try:
        raw = await client.get(key)
    finally:
        await client.aclose()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return None


def _build_requisite_payload(order) -> Optional[Dict[str, Any]]:
    """Serializable subset of the requisite for rendering in the admin card."""
    requisite = getattr(order, "requisite", None)
    if not requisite:
        return None
    option = getattr(order, "payment_option", None)
    return {
        "name": (option.name if option else None) or requisite.bank_name,
        "nickname": requisite.nickname,
        "bank_name": requisite.bank_name,
        "account_number": requisite.account_number,
        "account_holder": requisite.account_holder,
        "payment_method": requisite.payment_method.value if requisite.payment_method else None,
    }


def _build_merchant_payload(merchant) -> Optional[Dict[str, Any]]:
    if not merchant:
        return None
    return {
        "id": getattr(merchant, "id", None),
        "name": getattr(merchant, "name", None),
    }


@celery_app.task(
    name="app.workers.tasks.support_bot.send_receipt_to_support_bot",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_receipt_to_support_bot(self, order_id: int, moderation_id: int):
    """Forward a receipt awaiting moderation to the support-bot's admin chat.

    The bot replies with the Telegram ``message_id`` it created — we
    back-fill that into the ``receipt_moderations`` row so the bot can
    later edit the inline keyboard from a callback handler.
    """
    settings = get_settings()
    if not settings.SUPPORT_BOT_URL or not settings.SUPPORT_BOT_SECRET:
        logger.warning(
            "send_receipt_to_support_bot skipped: SUPPORT_BOT_URL or SUPPORT_BOT_SECRET is not set",
            order_id=order_id,
        )
        return

    async def _run() -> str:
        async with SessionLocal() as session:
            from app.modules.orders.models import Order
            from app.modules.receipts.models import ReceiptModeration

            order = await session.get(Order, order_id)
            if not order:
                return "order_not_found"
            if not order.receipt_file:
                return "receipt_file_missing"

            moderation = await session.get(ReceiptModeration, moderation_id)
            if not moderation:
                return "moderation_row_missing"
            if moderation.message_id is not None:
                # The task was retried after a successful first delivery.
                return "already_sent"

            # The latest receipt carries ``dispute_id`` when it's extra proof a
            # merchant attached to an open dispute (video/pdf request) rather than
            # a first-time check — the moderation card is worded differently for it.
            from app.modules.receipts.repository import ReceiptRepository

            latest_receipt = await ReceiptRepository(session).get_latest_for_order(order_id)
            is_dispute_evidence = bool(latest_receipt and latest_receipt.dispute_id)

            payload: Dict[str, Any] = {
                "chat_id": moderation.chat_id,
                "order_uuid": str(order.uuid),
                "external_id": order.external_id,
                "amount": float(order.amount),
                "currency": order.currency.value,
                "payment_method": order.payment_method.value,
                "file_paths": [order.receipt_file],
                "requisite": _build_requisite_payload(order),
                "merchant": _build_merchant_payload(getattr(order, "merchant", None)),
                "is_dispute_evidence": is_dispute_evidence,
            }

            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    f"{settings.SUPPORT_BOT_URL.rstrip('/')}/new_receipt_for_moderation",
                    json=payload,
                    headers={"X-Bot-Secret": settings.SUPPORT_BOT_SECRET},
                )
                resp.raise_for_status()
                body = resp.json() if resp.content else {}

            message_id = body.get("message_id")
            if isinstance(message_id, int):
                moderation.message_id = message_id
                session.add(moderation)
                await session.commit()
            return "sent"

    try:
        result = asyncio.run(_run())
    except Exception as exc:
        logger.warning(
            "send_receipt_to_support_bot failed",
            order_id=order_id,
            moderation_id=moderation_id,
            error=str(exc),
            attempt=self.request.retries + 1,
        )
        raise self.retry(exc=exc)

    if result == "sent":
        logger.info(
            "send_receipt_to_support_bot sent",
            order_id=order_id,
            moderation_id=moderation_id,
        )
        return

    logger.warning(
        "send_receipt_to_support_bot skipped",
        order_id=order_id,
        moderation_id=moderation_id,
        reason=result,
    )


async def _remind_stale_premoderation_async() -> str:
    """Re-ping the support-bot for receipt checks left without a reaction.

    A check is "stale" when its ``receipt_moderations`` row is still undecided
    (``decision IS NULL``), was delivered to Telegram (``message_id`` set), the
    owning order is still active (not terminal), and the time since the LAST
    reminder — or the original card, when never reminded — exceeds the
    ``premoderation_reminder_minutes`` platform setting. For each such row we ask
    the support-bot to reply to the original card and stamp ``reminded_at`` so the
    next tick waits a full interval again (recurring nudge until an admin clicks).
    """
    from datetime import timedelta

    from sqlalchemy import or_, select

    from app.common.enums.orders import FINAL_STATUSES
    from app.common.types import utcnow
    from app.modules.orders.models import Order
    from app.modules.receipts.models import ReceiptModeration
    from app.modules.settings.service import SettingsService

    settings = get_settings()
    if not settings.SUPPORT_BOT_URL or not settings.SUPPORT_BOT_SECRET:
        return "bot_unconfigured"

    async with SessionLocal() as session:
        minutes = await SettingsService(session).get_int("premoderation_reminder_minutes")
        if minutes <= 0:
            return "disabled"
        cutoff = utcnow() - timedelta(minutes=minutes)

        stmt = (
            select(ReceiptModeration, Order.external_id, Order.uuid)
            .join(Order, Order.id == ReceiptModeration.order_id)
            .where(
                ReceiptModeration.decision.is_(None),
                ReceiptModeration.message_id.isnot(None),
                ReceiptModeration.created_at <= cutoff,
                or_(
                    ReceiptModeration.reminded_at.is_(None),
                    ReceiptModeration.reminded_at <= cutoff,
                ),
                Order.status.notin_(list(FINAL_STATUSES)),
            )
            .order_by(ReceiptModeration.created_at.asc())
            .limit(100)
        )
        rows = list((await session.execute(stmt)).all())
        if not rows:
            return "none"

        sent = 0
        async with httpx.AsyncClient(timeout=15) as client:
            for moderation, external_id, order_uuid in rows:
                payload = {
                    "chat_id": moderation.chat_id,
                    "message_id": moderation.message_id,
                    "external_id": external_id,
                    "order_uuid": str(order_uuid),
                }
                try:
                    resp = await client.post(
                        f"{settings.SUPPORT_BOT_URL.rstrip('/')}/remind_premoderation",
                        json=payload,
                        headers={"X-Bot-Secret": settings.SUPPORT_BOT_SECRET},
                    )
                    resp.raise_for_status()
                except Exception as exc:
                    logger.warning(
                        "remind_premoderation delivery failed",
                        moderation_id=moderation.id,
                        order_id=moderation.order_id,
                        error=str(exc),
                    )
                    continue
                moderation.reminded_at = utcnow()
                session.add(moderation)
                sent += 1
        await session.commit()
        return f"reminded:{sent}"


@celery_app.task(
    name="remind_stale_premoderation_checks_task",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def remind_stale_premoderation_checks_task(self):
    """Periodic: nudge the support chat about receipt checks left unreacted.

    Idempotent per interval via ``receipt_moderations.reminded_at``. No-ops when
    the support bot is unconfigured or the reminder is disabled (minutes = 0).
    """
    try:
        result = asyncio.run(_remind_stale_premoderation_async())
    except Exception as exc:
        logger.warning("remind_stale_premoderation_checks_task failed", error=str(exc))
        raise self.retry(exc=exc)
    logger.info("remind_stale_premoderation_checks_task done", result=result)


async def _resolve_withdrawal_user(session, withdrawal):
    """Resolve the USER who created the withdrawal → ``(user_id, username)``.

    ``withdrawal.user_id`` is the platform ``users.id`` for traders/teamleads
    AND the merchant owner's ``users.id`` for current merchant withdrawals, so a
    single User lookup always gives the requesting account — no merchant-name /
    merchant-id mixing. Never raises; username is ``None`` when the row can't be
    resolved (legacy data).
    """
    from app.modules.users.models import User

    if not withdrawal.user_id:
        return None, None
    user = await session.get(User, withdrawal.user_id)
    return withdrawal.user_id, (user.username if user else None)


async def _build_withdrawal_payload(session, wr, chat_id: int) -> Dict[str, Any]:
    """Сериализуемые поля карточки вывода — общие для исходного уведомления и
    для апдейта решения (бот перерисовывает по ним карточку 1-в-1)."""
    user_id, user_login = await _resolve_withdrawal_user(session, wr)
    return {
        "chat_id": chat_id,
        "withdrawal_id": wr.id,
        "user_id": user_id,
        "user_login": user_login,
        "amount": float(wr.amount),
        "currency": getattr(wr.currency, "value", str(wr.currency)),
        "fee_amount": float(wr.fee_amount or 0),
        "destination_address": wr.destination_address,
    }


async def _notify_withdrawal_async(withdrawal_id: int) -> str:
    """Core logic of the withdrawal-notification task (module-level so it's
    unit-testable). Returns a short outcome string; ``"sent"`` means delivered,
    everything else is a logged no-op reason.
    """
    settings = get_settings()
    async with SessionLocal() as session:
        from app.modules.settings.service import SettingsService

        settings_service = SettingsService(session)
        if not await settings_service.get_bool("notify_withdrawal_requests"):
            return "disabled"
        chat_id_raw = await settings_service.get_str("notifications_chat_id")
        if not chat_id_raw:
            return "no_chat"
        try:
            chat_id = int(chat_id_raw)
        except ValueError:
            return "bad_chat"

        if not settings.SUPPORT_BOT_URL or not settings.SUPPORT_BOT_SECRET:
            return "bot_unconfigured"

        from app.modules.finance.models import WithdrawalRequest

        wr = await session.get(WithdrawalRequest, withdrawal_id)
        if not wr:
            return "withdrawal_not_found"

        payload = await _build_withdrawal_payload(session, wr, chat_id)

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.SUPPORT_BOT_URL.rstrip('/')}/notify_withdrawal",
                json=payload,
                headers={"X-Bot-Secret": settings.SUPPORT_BOT_SECRET},
            )
            resp.raise_for_status()
            body = resp.json() if resp.content else {}

        # Запоминаем message_id карточки, чтобы позже дописать в неё исход
        # модерации (approve/reject). Best-effort: сбой Redis не валит таску.
        message_id = body.get("message_id")
        if isinstance(message_id, int):
            try:
                await _redis_set_json(
                    _WITHDRAWAL_NOTIFY_KEY.format(id=wr.id),
                    {"chat_id": chat_id, "message_id": message_id},
                    _WITHDRAWAL_NOTIFY_TTL,
                )
            except Exception as exc:  # pragma: no cover — best-effort cache
                logger.warning(
                    "withdrawal notify message_id cache failed",
                    withdrawal_id=wr.id, error=str(exc),
                )
        return "sent"


@celery_app.task(
    name="app.workers.tasks.support_bot.notify_withdrawal_request",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def notify_withdrawal_request(self, withdrawal_id: int):
    """Notify the platform notifications group about a new withdrawal request.

    Best-effort telemetry, never part of the money path. No-ops cleanly when
    the ``notify_withdrawal_requests`` toggle is off, no ``notifications_chat_id``
    is configured, or the support-bot URL/secret are unset.
    """
    try:
        result = asyncio.run(_notify_withdrawal_async(withdrawal_id))
    except Exception as exc:
        logger.warning(
            "notify_withdrawal_request failed",
            withdrawal_id=withdrawal_id,
            error=str(exc),
            attempt=self.request.retries + 1,
        )
        raise self.retry(exc=exc)

    if result != "sent":
        logger.info(
            "notify_withdrawal_request skipped",
            withdrawal_id=withdrawal_id,
            reason=result,
        )


async def _notify_withdrawal_decided_async(withdrawal_id: int, decision: str) -> str:
    """Дописать исход модерации (approve/reject) в карточку вывода.

    ``decision`` — ``"approved"`` или ``"rejected"``. No-op, если исходную
    карточку не отправляли (тоггл выключен / нет чата) или её ``message_id``
    уже вытек из Redis по TTL. Best-effort, не часть денежного пути.
    """
    settings = get_settings()
    if not settings.SUPPORT_BOT_URL or not settings.SUPPORT_BOT_SECRET:
        return "bot_unconfigured"

    stored = await _redis_get_json(_WITHDRAWAL_NOTIFY_KEY.format(id=withdrawal_id))
    if not stored or stored.get("message_id") is None:
        return "no_card"  # карточку не слали или message_id истёк

    async with SessionLocal() as session:
        from app.modules.finance.models import WithdrawalRequest

        wr = await session.get(WithdrawalRequest, withdrawal_id)
        if not wr:
            return "withdrawal_not_found"

        payload = await _build_withdrawal_payload(session, wr, int(stored["chat_id"]))
        payload["message_id"] = stored["message_id"]
        payload["status"] = decision

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{settings.SUPPORT_BOT_URL.rstrip('/')}/notify_withdrawal_decided",
                json=payload,
                headers={"X-Bot-Secret": settings.SUPPORT_BOT_SECRET},
            )
            resp.raise_for_status()
        return "sent"


@celery_app.task(
    name="app.workers.tasks.support_bot.notify_withdrawal_decided",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def notify_withdrawal_decided(self, withdrawal_id: int, decision: str):
    """Append the approve/reject outcome to the withdrawal's notification card.

    Best-effort telemetry, never part of the money path. No-ops cleanly when the
    original card was never delivered or its message_id has expired from Redis.
    """
    try:
        result = asyncio.run(_notify_withdrawal_decided_async(withdrawal_id, decision))
    except Exception as exc:
        logger.warning(
            "notify_withdrawal_decided failed",
            withdrawal_id=withdrawal_id,
            decision=decision,
            error=str(exc),
            attempt=self.request.retries + 1,
        )
        raise self.retry(exc=exc)

    if result != "sent":
        logger.info(
            "notify_withdrawal_decided skipped",
            withdrawal_id=withdrawal_id,
            decision=decision,
            reason=result,
        )
