"""ReceiptService — owns the whole receipt lifecycle.

``upload()`` is the single entry point for "a receipt arrived" (merchant API,
dispute-bot, …): it validates, stores the file, runs the moderation pipeline,
appends the receipt + updates the order mirror atomically, and fires the
resulting effects. The upload surfaces (``OrderService.confirm_order`` etc.) are
thin: they resolve the order + read the bytes, then delegate here.

Storage, the moderation policy/checker pipeline and the side-effects live in
their own files (``storage`` / ``policies`` / ``moderation`` / ``effects``);
this service composes them.
"""
from typing import List, Optional

from app.common.constants.receipts import MAX_RECEIPTS_PER_ORDER
from app.common.enums.receipt_moderations import ModerationStatus
from app.common.enums.receipts import ReceiptSource, ReceiptUploader
from app.modules.base.service import BaseService
from app.modules.receipts.exceptions import DuplicateReceiptError, ReceiptLimitReachedError
from app.modules.receipts.models import Receipt
from app.modules.receipts.repository import ReceiptRepository


class ReceiptService(BaseService):
    # Per-order safety cap (OOM / abuse backstop). Disputes share the order's
    # receipts, so this bounds appeal evidence too.
    MAX_PER_ORDER = MAX_RECEIPTS_PER_ORDER

    def __init__(self, session):
        super().__init__(session)
        self.repository = ReceiptRepository(session)

    async def upload(
        self,
        *,
        order,
        merchant,
        content: bytes,
        filename: Optional[str],
        uploaded_by: ReceiptUploader = ReceiptUploader.MERCHANT,
        dispute_id: Optional[int] = None,
    ):
        """Full "a receipt was uploaded" use case. Returns the updated order.

        Steps: validate size → hash → status gate → moderation pipeline →
        dedup/cap pre-check → save file → (mirror + receipt + audit, one tx) →
        act on the moderation outcome (human review / auto-approve effects /
        auto-reject). Behaviour mirrors the historical ``confirm_order`` body —
        it's just owned here now.
        """
        from app.common.enums.orders import RECEIPT_UPLOADABLE_STATUSES, OrderStatus
        from app.common.types import utcnow
        from app.core.exceptions import ConflictException
        from app.core.logging import get_logger
        from app.modules.orders.repository import OrderRepository
        from app.modules.receipts.effects import (
            enqueue_merchant_proof_request,
            fire_receipt_approved,
        )
        from app.modules.receipts.moderation import ModerationOutcome, ReceiptModerationService
        from app.modules.receipts.storage import ReceiptStorage

        logger = get_logger(__name__)

        ReceiptStorage.validate_size(content)
        sha256 = ReceiptStorage.sha256(content)

        # Multiple receipts allowed while the order is still open; terminal
        # orders reject further uploads.
        if order.status not in RECEIPT_UPLOADABLE_STATUSES:
            raise ConflictException("Cannot confirm transfer for this order")

        # The moderation service owns the decision (policy + checkers + human
        # fallback) AND the human-review lifecycle. With no auto-checker enabled
        # it reproduces the historical premoderation behaviour exactly.
        moderation_service = ReceiptModerationService(self.session)
        moderation = await moderation_service.evaluate(order, merchant)
        review_required = moderation.outcome == ModerationOutcome.ESCALATE_HUMAN

        # Reject duplicate / cap-overflow BEFORE writing the file (no orphan).
        await self.assert_can_add(order.id, sha256)
        file_path = ReceiptStorage.save(order.uuid, content, filename)

        prev_status = order.status
        new_status = (
            order.status if order.status == OrderStatus.DISPUTED
            else OrderStatus.RECEIPT_UPLOADED
        )
        receipt_mod_status = (
            ModerationStatus.PENDING if review_required else ModerationStatus.NONE
        )
        source, actor = uploaded_by.source, uploaded_by.actor

        receipt_fields = {
            "receipt_file": file_path,
            "receipt_uploaded_at": utcnow(),
            "receipt_uploaded_by": uploaded_by.value,
        }
        async with self.session.begin_nested():
            if new_status != prev_status:
                # Genuine PENDING → RECEIPT_UPLOADED transition: route the status
                # write through the order's single funnel (carries the receipt
                # fields via extra_fields; non-financial, so no money moves). We
                # own the receipt row + audit + webhook below.
                from app.modules.orders.service import OrderService

                order = await OrderService(self.session).change_status(
                    order, new_status, extra_fields=receipt_fields,
                    audit_action=None, fire_callback=False,
                )
            else:
                # Re-upload to an already RECEIPT_UPLOADED / DISPUTED order — no
                # status transition, just refresh the receipt mirror fields.
                order = await OrderRepository(self.session).update(order.id, receipt_fields)
            receipt = await self.add_receipt(
                order_id=order.id, file_path=file_path, source=source,
                uploaded_by=actor, dispute_id=dispute_id, sha256=sha256,
                moderation_status=receipt_mod_status,
            )
            await self.audit_log(
                action="confirm_order", entity_type="order", entity_id=order.id,
                user_id=merchant.user_id,
                new_values={"status": new_status.value, "receipt_file": file_path},
            )

        # Whether the assigned trader has auto-check on — the fraud task then
        # owns the trader notification (skip the immediate one).
        trader_auto_check = await self._trader_auto_check(order.trader_id)

        if review_required:
            row = await moderation_service.create_pending(
                order, chat_id=moderation.support_chat_id,
            )
            try:
                from app.workers.celery_app import celery_app

                celery_app.send_task(
                    "app.workers.tasks.support_bot.send_receipt_to_support_bot",
                    args=[order.id, row.id], countdown=2,
                )
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning(
                    "send_receipt_to_support_bot enqueue failed (order_id=%s): %s",
                    order.id, exc,
                )
        elif moderation.outcome == ModerationOutcome.AUTO_APPROVE:
            fire_receipt_approved(
                order.id, notify_trader=not trader_auto_check, notify_countdown=2,
                receipt_id=getattr(receipt, "id", None),
            )
        elif moderation.outcome == ModerationOutcome.AUTO_REJECT:
            enqueue_merchant_proof_request(order.id, moderation.reject_reason or "request_pdf")

        # Merchant webhook on the genuine PENDING → RECEIPT_UPLOADED transition
        # (not on re-uploads to an already-RECEIPT_UPLOADED / DISPUTED order).
        # Best-effort: a broker hiccup must not fail an accepted receipt.
        if new_status != prev_status:
            try:
                from app.workers.celery_app import celery_app

                celery_app.send_task(
                    "app.workers.tasks.callbacks.send_order_callback", args=[order.id],
                )
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning(
                    "send_order_callback enqueue failed (order_id=%s): %s", order.id, exc,
                )

        return order

    async def _trader_auto_check(self, trader_user_id: Optional[int]) -> bool:
        if not trader_user_id:
            return False
        from sqlalchemy import select

        from app.modules.traders.models import Trader

        r = await self.session.execute(
            select(Trader.receipt_auto_check).where(Trader.user_id == trader_user_id)
        )
        return bool(r.scalar_one_or_none())

    async def add_receipt(
        self,
        *,
        order_id: int,
        file_path: str,
        source: ReceiptSource,
        uploaded_by: Optional[str] = None,
        dispute_id: Optional[int] = None,
        sha256: Optional[str] = None,
        file_size: Optional[int] = None,
        mime: Optional[str] = None,
        moderation_status: ModerationStatus = ModerationStatus.NONE,
    ) -> Receipt:
        """Append a receipt to an order (optionally linked to a dispute).

        Two invariants:
          * **dedup** — the same file (by sha256) is never stored twice on one
            order (409); the fraud-check also keys on sha256.
          * **cap** — at most ``MAX_PER_ORDER`` receipts per order.
        """
        if sha256:
            existing = await self.repository.find_by_sha_for_order(order_id, sha256)
            if existing is not None:
                raise DuplicateReceiptError(
                    "This receipt was already uploaded for this order"
                )

        if await self.repository.count_for_order(order_id) >= self.MAX_PER_ORDER:
            raise ReceiptLimitReachedError(
                f"Receipt limit reached ({self.MAX_PER_ORDER}) for this order"
            )

        return await self.repository.create({
            "order_id": order_id,
            "dispute_id": dispute_id,
            "file_path": file_path,
            "sha256": sha256,
            "file_size": file_size,
            "mime": mime,
            "source": source,
            "uploaded_by": uploaded_by,
            "moderation_status": moderation_status,
        })

    async def assert_can_add(self, order_id: int, sha256: Optional[str]) -> None:
        """Pre-flight check before writing the file to disk: reject a duplicate
        (same sha on the order) or a cap overflow up-front, so a rejected upload
        never leaves an orphaned file behind. ``add_receipt`` re-checks defensively
        (race-safe) but the happy path is already validated here."""
        if sha256:
            if await self.repository.find_by_sha_for_order(order_id, sha256) is not None:
                raise DuplicateReceiptError(
                    "This receipt was already uploaded for this order"
                )
        if await self.repository.count_for_order(order_id) >= self.MAX_PER_ORDER:
            raise ReceiptLimitReachedError(
                f"Receipt limit reached ({self.MAX_PER_ORDER}) for this order"
            )

    async def list_for_order(
        self, order_id: int, *, visible_to_trader_only: bool = False
    ) -> List[Receipt]:
        return await self.repository.list_for_order(
            order_id, visible_to_trader_only=visible_to_trader_only
        )

    async def list_for_dispute(self, dispute_id: int) -> List[Receipt]:
        return await self.repository.list_for_dispute(dispute_id)

    async def get_by_uuid(self, uuid_str: str) -> Optional[Receipt]:
        return await self.repository.get_by_uuid(uuid_str)
