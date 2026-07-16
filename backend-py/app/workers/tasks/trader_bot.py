import asyncio

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.infrastructure.db.session import WorkerSessionLocal as SessionLocal
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


async def _fetch_trader_group_id(session, trader_user_id: int) -> int | None:
    from sqlalchemy import select
    from app.modules.traders.models import Trader

    result = await session.execute(
        select(Trader.telegram_group_id).where(Trader.user_id == trader_user_id)
    )
    return result.scalar_one_or_none()


def _build_requisite_payload(order) -> dict | None:
    """Build serializable requisite info to ship to the trader bot."""
    requisite = order.requisite
    if not requisite:
        return None
    option = order.payment_option
    return {
        "name": (option.name if option else None) or requisite.bank_name,
        "nickname": requisite.nickname,
        "bank_name": requisite.bank_name,
        "account_number": requisite.account_number,
        "account_holder": requisite.account_holder,
        "payment_method": requisite.payment_method.value if requisite.payment_method else None,
    }


async def _build_receipt_check_payload(session, receipt_check_id: int | None) -> dict | None:
    """Serialise the receipt-check verdict for the trader bot. Returns None
    when no check is attached or the row is missing — the bot then renders
    the message without a verification line."""
    if receipt_check_id is None:
        return None
    from app.modules.receipt_checks.models import ReceiptCheck

    check = await session.get(ReceiptCheck, receipt_check_id)
    if not check:
        return None
    status_val = check.status.value if hasattr(check.status, "value") else str(check.status)
    return {
        "status": status_val,
        "is_clean": check.is_clean,
        "error_code": check.error_code,
    }


@celery_app.task(
    name="app.workers.tasks.trader_bot.notify_trader_new_receipt",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def notify_trader_new_receipt(self, order_id: int, receipt_check_id: int | None = None):
    settings = get_settings()
    if not settings.TRADER_BOT_URL or not settings.TRADER_BOT_SECRET:
        logger.warning(
            "notify_trader_new_receipt skipped: TRADER_BOT_URL or TRADER_BOT_SECRET is not set",
            order_id=order_id,
        )
        return

    async def _run() -> str:
        async with SessionLocal() as session:
            from app.modules.orders.models import Order

            order = await session.get(Order, order_id)
            if not order:
                return "order_not_found"
            if not order.trader_id:
                return "trader_id_missing"
            if not order.receipt_file:
                return "receipt_file_missing"

            group_id = await _fetch_trader_group_id(session, order.trader_id)
            if not group_id:
                return "telegram_group_id_missing"

            # The latest receipt carries ``dispute_id`` when it's extra proof a
            # merchant attached to an open dispute (video/pdf request) rather than
            # a first-time check — the bot words the message differently for it.
            from app.modules.receipts.repository import ReceiptRepository

            latest_receipt = await ReceiptRepository(session).get_latest_for_order(order_id)
            is_dispute_evidence = bool(latest_receipt and latest_receipt.dispute_id)

            payload = {
                "chat_id": group_id,
                "order_uuid": str(order.uuid),
                "amount": float(order.amount),
                "currency": order.currency.value,
                "payment_method": order.payment_method.value,
                "file_paths": [order.receipt_file],
                "requisite": _build_requisite_payload(order),
                "receipt_check": await _build_receipt_check_payload(session, receipt_check_id),
                "is_dispute_evidence": is_dispute_evidence,
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{settings.TRADER_BOT_URL.rstrip('/')}/new_check_uploaded",
                    json=payload,
                    headers={"X-Bot-Secret": settings.TRADER_BOT_SECRET},
                )
                resp.raise_for_status()
            return "sent"

    try:
        result = asyncio.run(_run())
    except Exception as exc:
        logger.warning(
            "notify_trader_new_receipt failed",
            order_id=order_id,
            error=str(exc),
            attempt=self.request.retries + 1,
        )
        raise self.retry(exc=exc)

    if result == "sent":
        logger.info("notify_trader_new_receipt sent", order_id=order_id)
        return

    logger.warning(
        "notify_trader_new_receipt skipped",
        order_id=order_id,
        reason=result,
    )


def _build_doliv_requisite_payload(doliv) -> dict | None:
    """Requisite info for the долив receipt notification, from the долив's stored
    snapshot (доливы have no order.requisite relationship)."""
    if not (doliv.req_number or doliv.req_holder or doliv.req_extra):
        return None
    return {
        "name": doliv.req_extra,
        "bank_name": doliv.req_extra,
        "account_number": doliv.req_number,
        "account_holder": doliv.req_holder,
        "payment_method": doliv.payment_method.value if doliv.payment_method else None,
    }


@celery_app.task(
    name="app.workers.tasks.trader_bot.notify_requester_doliv_receipt",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def notify_requester_doliv_receipt(self, doliv_id: int):
    """Deliver a доливщик's receipt to the REQUESTER's telegram bot (the trader
    whose requisite the долив filled)."""
    settings = get_settings()
    if not settings.TRADER_BOT_URL or not settings.TRADER_BOT_SECRET:
        logger.warning(
            "notify_requester_doliv_receipt skipped: TRADER_BOT_URL or TRADER_BOT_SECRET is not set",
            doliv_id=doliv_id,
        )
        return

    async def _run() -> str:
        async with SessionLocal() as session:
            from app.modules.payouts.models import Payout

            doliv = await session.get(Payout, doliv_id)
            if not doliv or not doliv.is_doliv:
                return "doliv_not_found"
            if not doliv.requester_trader_id:
                return "requester_missing"
            if not doliv.receipt_file:
                return "receipt_file_missing"

            group_id = await _fetch_trader_group_id(session, doliv.requester_trader_id)
            if not group_id:
                return "telegram_group_id_missing"

            payload = {
                "chat_id": group_id,
                "doliv_uuid": str(doliv.uuid),
                "amount": float(doliv.amount),
                "currency": doliv.currency.value,
                "payment_method": doliv.payment_method.value,
                "file_paths": [doliv.receipt_file],
                "requisite": _build_doliv_requisite_payload(doliv),
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{settings.TRADER_BOT_URL.rstrip('/')}/doliv_check",
                    json=payload,
                    headers={"X-Bot-Secret": settings.TRADER_BOT_SECRET},
                )
                resp.raise_for_status()
            return "sent"

    try:
        result = asyncio.run(_run())
    except Exception as exc:
        logger.warning(
            "notify_requester_doliv_receipt failed",
            doliv_id=doliv_id,
            error=str(exc),
            attempt=self.request.retries + 1,
        )
        raise self.retry(exc=exc)

    if result == "sent":
        logger.info("notify_requester_doliv_receipt sent", doliv_id=doliv_id)
        return

    logger.warning(
        "notify_requester_doliv_receipt skipped",
        doliv_id=doliv_id,
        reason=result,
    )


@celery_app.task(
    name="app.workers.tasks.trader_bot.notify_trader_new_dispute",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def notify_trader_new_dispute(self, dispute_id: int):
    settings = get_settings()
    if not settings.TRADER_BOT_URL or not settings.TRADER_BOT_SECRET:
        logger.warning(
            "notify_trader_new_dispute skipped: TRADER_BOT_URL or TRADER_BOT_SECRET is not set",
            dispute_id=dispute_id,
        )
        return

    async def _run() -> str:
        async with SessionLocal() as session:
            from app.modules.disputes.models import Dispute
            from app.modules.orders.models import Order

            dispute = await session.get(Dispute, dispute_id)
            if not dispute:
                return "dispute_not_found"

            order = await session.get(Order, dispute.order_id)
            if not order:
                return "order_not_found"
            if not order.trader_id:
                return "trader_id_missing"

            group_id = await _fetch_trader_group_id(session, order.trader_id)
            if not group_id:
                return "telegram_group_id_missing"

            file_paths: list[str] = []
            if isinstance(dispute.evidence_files, list):
                file_paths = [f for f in dispute.evidence_files if f]

            payload = {
                "chat_id": group_id,
                "dispute_uuid": str(dispute.uuid),
                "order_uuid": str(order.uuid),
                "reason": dispute.reason.value,
                "description": dispute.description,
                "amount": float(order.amount),
                "currency": order.currency.value,
                "file_paths": file_paths,
                "requisite": _build_requisite_payload(order),
            }
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"{settings.TRADER_BOT_URL.rstrip('/')}/new_dispute",
                    json=payload,
                    headers={"X-Bot-Secret": settings.TRADER_BOT_SECRET},
                )
                resp.raise_for_status()
            return "sent"

    try:
        result = asyncio.run(_run())
    except Exception as exc:
        logger.warning(
            "notify_trader_new_dispute failed",
            dispute_id=dispute_id,
            error=str(exc),
            attempt=self.request.retries + 1,
        )
        raise self.retry(exc=exc)

    if result == "sent":
        logger.info("notify_trader_new_dispute sent", dispute_id=dispute_id)
        return

    logger.warning(
        "notify_trader_new_dispute skipped",
        dispute_id=dispute_id,
        reason=result,
    )


@celery_app.task(
    name="app.workers.tasks.trader_bot.broadcast_message_to_all_traders",
    bind=True,
    max_retries=0,  # NEVER re-run the whole broadcast — that would double-send
)
def broadcast_message_to_all_traders(self, broadcast_id: int) -> None:
    """Send one admin broadcast's text to every trader in its audience — one DM
    per recipient, throttled under Telegram's ~30 msg/s cap. Per-recipient
    failures (bot blocked, chat gone, …) are counted and never abort the run.
    Records delivered/failed on the Broadcast row and marks it done."""
    from app.common.enums.broadcasts import BroadcastStatus
    from app.common.types import utcnow
    from app.modules.broadcasts.models import Broadcast
    from app.modules.broadcasts.repository import BroadcastRepository

    async def _finalize(status: BroadcastStatus, *, delivered: int = 0, failed: int = 0) -> None:
        async with SessionLocal() as session:
            async with session.begin():
                b = await session.get(Broadcast, broadcast_id)
                if b is not None:
                    b.delivered = delivered
                    b.failed = failed
                    b.status = status
                    b.finished_at = utcnow()

    async def _run() -> None:
        settings = get_settings()
        async with SessionLocal() as session:
            async with session.begin():
                broadcast = await session.get(Broadcast, broadcast_id)
                if broadcast is None:
                    logger.warning("broadcast %s vanished", broadcast_id)
                    return
                broadcast.status = BroadcastStatus.SENDING
                text = broadcast.text
                chat_ids = await BroadcastRepository(session).recipient_chat_ids(
                    broadcast.audience
                )

        # Bot integration missing → config error (FAILED), UNLESS there was simply
        # nobody to reach (legitimately DONE 0/0).
        if not settings.TRADER_BOT_URL or not settings.TRADER_BOT_SECRET:
            logger.warning("broadcast %s: trader bot URL/secret not configured", broadcast_id)
            await _finalize(BroadcastStatus.FAILED if chat_ids else BroadcastStatus.DONE)
            return

        delivered = 0
        failed = 0
        url = f"{settings.TRADER_BOT_URL.rstrip('/')}/broadcast"
        headers = {"X-Bot-Secret": settings.TRADER_BOT_SECRET}
        async with httpx.AsyncClient(timeout=15) as client:
            for chat_id in chat_ids:
                try:
                    resp = await client.post(
                        url, json={"chat_id": chat_id, "text": text}, headers=headers
                    )
                    resp.raise_for_status()
                    delivered += 1
                except Exception as exc:
                    failed += 1
                    logger.warning(
                        "broadcast %s: send to chat %s failed: %s",
                        broadcast_id, chat_id, exc,
                    )
                # ~20 msg/s — comfortably under Telegram's 30/s global cap.
                await asyncio.sleep(0.05)

        await _finalize(BroadcastStatus.DONE, delivered=delivered, failed=failed)

    try:
        asyncio.run(_run())
    except Exception as exc:  # do NOT retry (max_retries=0 avoids double-send)
        logger.exception("broadcast %s failed: %s", broadcast_id, exc)
        # Never leave the row stuck in a non-terminal (pending/sending) state.
        try:
            asyncio.run(_finalize(BroadcastStatus.FAILED))
        except Exception:  # pragma: no cover — defensive
            logger.exception("broadcast %s: could not mark FAILED", broadcast_id)
