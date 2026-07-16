from typing import List, Optional, Tuple

from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.disputes import DisputeStatus
from app.common.enums.receipts import ReceiptUploader
from app.common.enums.orders import OrderStatus
from app.common.enums.users import UserRole
from app.common.types import utcnow
from app.core.exceptions import ForbiddenException, NotFoundException, ValidationException
from app.modules.base.service import BaseService
from app.modules.disputes.models import Dispute
from app.modules.disputes.repository import DisputeRepository
from app.modules.disputes.schemas import DisputeCreate
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.orders.repository import OrderRepository

_DISPUTABLE_STATUSES = {
    OrderStatus.SUCCESS,
    OrderStatus.FAILED,
    OrderStatus.CANCELED,
    OrderStatus.PENDING,
    OrderStatus.RECEIPT_UPLOADED,
}

# Premoderation / request-proof disputes may only OPEN on an ACTIVE order.
# Reopening a settled or terminal order (SUCCESS/FAILED/CANCELED) into DISPUTED
# runs reconcile_for_dispute and REVERSES the settlement — and the premoderation
# open is reachable trader-initiated (request_proof_from_trader_group), which
# could weaponise it to claw back a completed payin and then self-reject the
# dispute to reclaim the collateral. This set is deliberately stricter than
# _DISPUTABLE_STATUSES (the merchant/admin explicit-dispute paths intentionally
# allow SUCCESS); the proof path is for orders still under review.
_PREMODERATION_OPENABLE_STATUSES = {
    OrderStatus.PENDING,
    OrderStatus.RECEIPT_UPLOADED,
}

# Resolution text stamped when a trader decides a dispute via self-service
# (admin resolutions carry caller-supplied free text instead).
TRADER_ACCEPT_RESOLUTION_TEXT = "Принято трейдером"
TRADER_REJECT_RESOLUTION_TEXT = "Отклонено трейдером"


class DisputeService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = DisputeRepository(session)
        self.order_repository = OrderRepository(session)

    async def get_all(self, skip: int = 0, limit: int = 100) -> List[Dispute]:
        return await self.repository.get_all(skip, limit)

    async def list_admin(
        self,
        *,
        skip: int = 0,
        limit: int = 100,
        id_search: Optional[str] = None,
        trader_login: Optional[str] = None,
        merchant_login: Optional[str] = None,
        status: Optional[str] = None,
        reason: Optional[str] = None,
        payment_method: Optional[str] = None,
        amount_from: Optional[float] = None,
        amount_to: Optional[float] = None,
    ) -> List["DisputeResponse"]:
        """Return enriched dispute responses including trader/merchant logins and order info."""
        from app.modules.disputes.schemas import DisputeResponse
        from app.modules.users.models import User as UserModel

        disputes = await self.repository.list_admin(
            skip=skip,
            limit=limit,
            id_search=id_search,
            trader_login=trader_login,
            merchant_login=merchant_login,
            status=status,
            reason=reason,
            payment_method=payment_method,
            amount_from=amount_from,
            amount_to=amount_to,
        )
        if not disputes:
            return []

        from sqlalchemy import select as _sa_select

        order_ids = [d.order_id for d in disputes]
        orders_result = await self.session.execute(
            _sa_select(Order).where(Order.id.in_(order_ids))
        )
        orders_by_id = {o.id: o for o in orders_result.scalars().all()}

        merchant_ids = {o.merchant_id for o in orders_by_id.values()}
        merchants_result = await self.session.execute(
            _sa_select(Merchant).where(Merchant.id.in_(merchant_ids))
        ) if merchant_ids else None
        merchants_by_id = {}
        if merchants_result is not None:
            merchants_by_id = {m.id: m for m in merchants_result.scalars().all()}

        user_ids = {m.user_id for m in merchants_by_id.values()}
        user_ids.update(o.trader_id for o in orders_by_id.values() if o.trader_id)
        users_by_id = {}
        if user_ids:
            users_result = await self.session.execute(
                _sa_select(UserModel).where(UserModel.id.in_(user_ids))
            )
            users_by_id = {u.id: u for u in users_result.scalars().all()}

        responses: List["DisputeResponse"] = []
        for d in disputes:
            resp = DisputeResponse.model_validate(d, from_attributes=True)
            order = orders_by_id.get(d.order_id)
            if order:
                resp.order_uuid = str(order.uuid) if order.uuid else None
                resp.order_external_id = order.external_id
                resp.order_amount = float(order.amount) if order.amount is not None else None
                resp.order_payment_method = (
                    order.payment_method.value if hasattr(order.payment_method, "value") else order.payment_method
                )
                trader_user = users_by_id.get(order.trader_id) if order.trader_id else None
                resp.trader_login = trader_user.username if trader_user else None
            merchant = merchants_by_id.get(d.merchant_id)
            if merchant:
                merchant_user = users_by_id.get(merchant.user_id)
                resp.merchant_login = merchant_user.username if merchant_user else None
            responses.append(resp)
        return responses

    async def list_merchant_disputes(
        self, merchant_id: int, *, skip: int = 0, limit: int = 50
    ) -> List[Dispute]:
        return await self.repository.list_for_merchant(merchant_id, skip=skip, limit=limit)

    async def _collect_dispute_evidence(
        self,
        attachments: Optional[List[Tuple[bytes, Optional[str]]]],
        evidence_urls: Optional[List[str]],
    ) -> List[Tuple[bytes, Optional[str]]]:
        """Validate uploaded files + download/validate linked files, returning a
        sha-deduplicated ``[(content, filename)]`` list. Raises ``ValidationException``
        on any bad input — callers run this before mutating anything."""
        from app.modules.receipts.download import fetch_evidence
        from app.modules.receipts.storage import ReceiptStorage

        collected: List[Tuple[bytes, Optional[str]]] = []
        seen: set = set()

        def _add(content: bytes, filename: Optional[str]) -> None:
            sha = ReceiptStorage.sha256(content)
            if sha in seen:
                return
            seen.add(sha)
            collected.append((content, filename))

        for content, filename in attachments or []:
            ReceiptStorage.validate_size(content)
            ReceiptStorage.validate_format(content, filename)
            _add(content, filename)

        for url in evidence_urls or []:
            downloaded = await fetch_evidence(url)  # SSRF + size + format guarded
            _add(downloaded.content, downloaded.filename)

        return collected

    async def _attach_evidence_to_dispute(
        self,
        *,
        dispute: Dispute,
        order: Order,
        merchant: Merchant,
        evidence_to_store: List[Tuple[bytes, Optional[str]]],
        uploaded_by: ReceiptUploader,
    ) -> None:
        """Store already-validated evidence as dispute-linked receipts via the
        same path as the dispute-bot (``ReceiptService.upload`` → premoderation +
        store + effects), then re-mirror the dispute's ``evidence_files`` onto our
        trusted stored paths (the trader-bot / cascade forwarders read only our
        paths, never raw merchant strings). The order must already be DISPUTED.

        Shared by the open core (``_open_dispute_for_order``) and the merchant's
        re-attach to an EXISTING open dispute (``open_dispute_by_merchant`` when a
        dispute already exists). ``list_for_dispute`` returns the FULL set, so the
        mirror accumulates existing + newly-appended evidence."""
        if not evidence_to_store:
            return

        from app.modules.receipts.service import ReceiptService

        # Re-fetch the order for the upload status gate (it's DISPUTED by now).
        order = await self.order_repository.get(order.id)
        receipt_service = ReceiptService(self.session)
        for content, filename in evidence_to_store:
            await receipt_service.upload(
                order=order, merchant=merchant, content=content,
                filename=filename, uploaded_by=uploaded_by, dispute_id=dispute.id,
            )
        await self._mirror_dispute_evidence_files(dispute.id)

    async def _mirror_dispute_evidence_files(self, dispute_id: int) -> None:
        """Re-mirror a dispute's ``evidence_files`` onto our trusted stored receipt
        paths (``list_for_dispute`` returns the FULL set, so it accumulates). Keeps
        ``evidence_count`` and the trader-bot / cascade forwarders reading only our
        UPLOAD_DIR paths — never raw merchant strings. Called after ANY receipt is
        linked to the dispute (open-time attach, create-endpoint re-attach, and the
        dedicated ``/receipt`` upload) so the count stays consistent across paths."""
        from app.modules.receipts.service import ReceiptService

        rows = await ReceiptService(self.session).list_for_dispute(dispute_id)
        await self.repository.update(
            dispute_id, {"evidence_files": [r.file_path for r in rows if r.file_path]}
        )

    async def open_dispute_by_merchant(
        self,
        merchant: Merchant,
        data: DisputeCreate,
        order_id: Optional[str] = None,
        external_id: Optional[str] = None,
        attachments: Optional[List[Tuple[bytes, Optional[str]]]] = None,
        evidence_urls: Optional[List[str]] = None,
    ) -> Dispute:
        """Open a dispute by a merchant.

        ``attachments`` are already-read ``(bytes, filename)`` evidence files;
        ``evidence_urls`` are links we download server-side. Both are format-
        validated (and links SSRF-guarded + downloaded) **before** the dispute is
        opened — any bad input fails the whole request, nothing is created. The
        validated files are then attached as dispute-linked receipts through the
        same premoderation pipeline as the dispute-bot.
        """
        if not order_id and not external_id:
            raise ValidationException("Either order_id or external_id must be provided")

        # Find order
        if order_id:
            order = await self.order_repository.get_by_uuid_and_merchant(order_id, merchant.id)
            error_msg = f"Order {order_id} not found"
        else:
            order = await self.order_repository.get_by_external_id_and_merchant(external_id, merchant.id)
            error_msg = f"Order with external ID {external_id} not found"

        if not order:
            raise NotFoundException(error_msg)

        # A dispute already exists → this is a RE-ATTACH: append the new check(s)
        # to the open dispute instead of erroring, so the merchant can keep adding
        # evidence through the same create endpoint (e.g. to fulfil a pdf/video
        # request) without tracking whether a dispute exists yet. Checked before
        # the disputable-status gate because an order with an open dispute is
        # already DISPUTED (not in _DISPUTABLE_STATUSES). The reason is fixed at
        # open — a re-attach never changes it.
        existing_dispute = await self.repository.get_by_order_id(order.id)
        if existing_dispute:
            if existing_dispute.status != DisputeStatus.OPEN:
                raise ValidationException("Dispute is already closed")
            evidence_to_store = await self._collect_dispute_evidence(attachments, evidence_urls)
            await self._attach_evidence_to_dispute(
                dispute=existing_dispute, order=order, merchant=merchant,
                evidence_to_store=evidence_to_store, uploaded_by=ReceiptUploader.MERCHANT,
            )
            return existing_dispute

        if order.status not in _DISPUTABLE_STATUSES:
            raise ValidationException(
                f"Cannot open dispute for order in status {order.status.value}. "
                f"Allowed: {', '.join(s.value for s in _DISPUTABLE_STATUSES)}"
            )

        if order.trader_id is None:
            raise ValidationException(
                "Cannot open a dispute for an order that was never assigned to a "
                "trader (no requisite was issued)."
            )

        # Gather + validate ALL evidence BEFORE any mutation (sync-strict):
        # bad format / unsafe or unreachable link / oversize → the whole request
        # fails and no dispute is created. Identical files (same sha) collapse.
        evidence_to_store = await self._collect_dispute_evidence(attachments, evidence_urls)

        return await self._open_dispute_for_order(
            order=order,
            merchant=merchant,
            reason=data.reason,
            initiator_type=UserRole.MERCHANT,
            initiator_id=merchant.id,
            audit_user_id=merchant.user_id,
            evidence_to_store=evidence_to_store,
            evidence_files_input=data.evidence_files,
            uploaded_by=ReceiptUploader.MERCHANT,
        )

    async def open_dispute_by_admin(
        self,
        admin_id: int,
        reason: "DisputeReason",
        order_uuid: str,
        attachments: Optional[List[Tuple[bytes, Optional[str]]]] = None,
        evidence_urls: Optional[List[str]] = None,
    ) -> Dispute:
        """Admin opens a dispute on any order (by its public UUID). Same gates,
        money reconcile, evidence pipeline and notifications as the merchant
        path — only the initiator (ADMIN) and the unscoped order lookup differ.
        ``external_id`` is intentionally not accepted: it's unique only per
        merchant, so the UUID is the global handle the admin addresses."""
        order = await self.order_repository.get_by_uuid(order_uuid)
        if not order:
            raise NotFoundException(f"Order {order_uuid} not found")

        if order.status not in _DISPUTABLE_STATUSES:
            raise ValidationException(
                f"Cannot open dispute for order in status {order.status.value}. "
                f"Allowed: {', '.join(s.value for s in _DISPUTABLE_STATUSES)}"
            )
        if order.trader_id is None:
            raise ValidationException(
                "Cannot open a dispute for an order that was never assigned to a "
                "trader (no requisite was issued)."
            )
        if await self.repository.get_by_order_id(order.id):
            raise ValidationException("Dispute already exists for this order")

        merchant = await self.session.get(Merchant, order.merchant_id)
        if merchant is None:
            raise NotFoundException("Order has no merchant")

        evidence_to_store = await self._collect_dispute_evidence(attachments, evidence_urls)

        return await self._open_dispute_for_order(
            order=order,
            merchant=merchant,
            reason=reason,
            initiator_type=UserRole.ADMIN,
            initiator_id=admin_id,
            audit_user_id=admin_id,
            evidence_to_store=evidence_to_store,
            evidence_files_input=[],
            uploaded_by=ReceiptUploader.SYSTEM,
        )

    async def _open_dispute_for_order(
        self,
        *,
        order: Order,
        merchant: Merchant,
        reason: "DisputeReason",
        initiator_type: UserRole,
        initiator_id: int,
        audit_user_id: int,
        evidence_to_store: List[Tuple[bytes, Optional[str]]],
        evidence_files_input: Optional[List[str]] = None,
        uploaded_by: ReceiptUploader = ReceiptUploader.MERCHANT,
    ) -> Dispute:
        """Shared dispute-open core (post-validation): create the dispute, move
        the order to DISPUTED, reconcile the money, audit, attach evidence as
        dispute-linked receipts, and fire trader/cascade/merchant notifications.
        Callers resolve+gate the order and collect evidence first."""
        pre_status = order.status

        dispute_data = {
            "order_id": order.id,
            "merchant_id": merchant.id,
            "initiator_type": initiator_type,
            "initiator_id": initiator_id,
            "status": DisputeStatus.OPEN,
            "reason": reason,
            "evidence_files": evidence_files_input,
            "assigned_user_type": UserRole.TRADER,
            "assigned_user_id": order.trader_id,
        }

        async with self.session.begin_nested():
            dispute = await self.repository.create(dispute_data)

            await self.audit_log(
                action="open_dispute",
                entity_type="dispute",
                entity_id=dispute.id,
                user_id=audit_user_id,
                new_values={
                    "order_id": order.id,
                    "reason": reason,
                    "pre_dispute_status": pre_status.value,
                },
            )

        # Order → DISPUTED + escrow normalisation through the single status
        # funnel: it re-fetches merchant/trader and runs reconcile_for_dispute on
        # the pre-dispute status. Requisite turnover and teamlead rewards are NOT
        # touched on open — they're adjusted only when the dispute is DECIDED
        # (resolve keeps a SUCCESS order counted / pays a newly-won one; reject
        # removes a previously-SUCCESS one). We own the dispute-row audit above
        # and the merchant callback below, so the funnel does neither.
        from app.modules.orders.service import OrderService

        await OrderService(self.session).change_status(
            order, OrderStatus.DISPUTED, actor_id=audit_user_id,
            audit_action=None, fire_callback=False,
        )

        # Attach evidence as dispute-linked receipts (the order is now DISPUTED).
        await self._attach_evidence_to_dispute(
            dispute=dispute, order=order, merchant=merchant,
            evidence_to_store=evidence_to_store, uploaded_by=uploaded_by,
        )

        from app.workers.tasks.trader_bot import notify_trader_new_dispute
        notify_trader_new_dispute.apply_async(args=[dispute.id], countdown=2)

        # Always enqueue the forward task — it short-circuits if the order
        # wasn't filled via cascade, so non-cascade disputes are a free no-op.
        # We swallow broker errors: the dispute is already committed and the
        # task is best-effort cascade plumbing — a broker outage must not
        # surface as a 500 to the caller who just opened a dispute.
        try:
            from app.workers.celery_app import celery_app
            celery_app.send_task(
                "app.workers.tasks.cascade.forward_dispute_to_provider",
                args=[dispute.id],
            )
        except Exception as exc:  # pragma: no cover — defensive
            import logging
            logging.getLogger(__name__).warning(
                "forward_dispute_to_provider enqueue failed (dispute_id=%s): %s",
                dispute.id, exc,
            )

        self._notify_merchant_order_callback(order.id)

        return dispute

    async def open_dispute_from_premoderation(
        self,
        order: "Order",
        decision: "ModerationDecision",
        *,
        moderator_user_id: Optional[int] = None,
        initiator_type: UserRole = UserRole.ADMIN,
        initiator_id: Optional[int] = None,
    ) -> Optional[Dispute]:
        """Open a premoderation-initiated dispute on an active order.

        Triggered when an admin, reviewing a receipt in the support-bot, asks
        the merchant for a PDF / video instead of accepting. The order is
        pulled into a dispute so it:
          * does NOT auto-expire (DISPUTED is excluded from the TTL sweep),
          * keeps its requisite-limit capacity (DISPUTED stays "active"),
          * keeps the trader's collateral frozen in ESCROW
            (``reconcile_for_dispute`` is a no-op for active pre-statuses).

        The dispute uses the ordinary OPEN → RESOLVED/REJECTED lifecycle; the
        ``substatus`` records what was requested. ``initiator_type=ADMIN``.

        Idempotent: if a dispute already exists for the order, returns it
        unchanged (a re-fired premoderation callback must not 500). Returns
        ``None`` if the order has no trader (nothing to dispute) — the caller
        treats that as "skip", same as any best-effort side effect.
        """
        from app.common.enums.disputes import DisputeReason, DisputeSubstatus
        from app.common.enums.receipt_moderations import ModerationDecision

        _SUBSTATUS_BY_DECISION = {
            ModerationDecision.REQUEST_PDF: DisputeSubstatus.PDF_REQUESTED,
            ModerationDecision.REQUEST_VIDEO: DisputeSubstatus.VIDEO_REQUESTED,
        }
        substatus = _SUBSTATUS_BY_DECISION.get(decision)
        if substatus is None:
            # ACCEPT or anything non-proof — nothing to open.
            return None

        if order.trader_id is None:
            # No collateral / no trader to assign — can't run resolve/reject later.
            return None

        # Idempotency: one dispute per order (DB also has a UNIQUE on order_id).
        existing = await self.repository.get_by_order_id(order.id)
        if existing:
            # A CLOSED dispute must NOT be revived by a late premoderation
            # decision — return None so the caller skips the merchant proof nudge
            # (re-notifying on an already-resolved/settled order is wrong).
            if existing.status != DisputeStatus.OPEN:
                return None
            # An OPEN dispute already exists (a second evidence request, e.g.
            # PDF after VIDEO): refresh its substatus to the newly-requested one
            # instead of silently dropping the request, then return it.
            if existing.substatus != substatus:
                async with self.session.begin_nested():
                    existing = await self.repository.update(
                        existing.id, {"substatus": substatus}
                    )
                    await self.audit_log(
                        action="update_dispute_substatus",
                        entity_type="dispute",
                        entity_id=existing.id,
                        user_id=moderator_user_id,
                        new_values={"substatus": substatus.value},
                    )
            return existing

        # Gate: only open on an active order. A settled/terminal order must never
        # be pulled into DISPUTED here (that reverses the settlement). Skip
        # (return None) — the admin moderation caller treats None as "nothing to
        # nudge", and request_proof_from_trader_group raises a ValidationException.
        if order.status not in _PREMODERATION_OPENABLE_STATUSES:
            return None

        pre_status = order.status

        dispute_data = {
            "order_id": order.id,
            "merchant_id": order.merchant_id,
            "initiator_type": initiator_type,
            "initiator_id": initiator_id if initiator_id is not None else moderator_user_id,
            "status": DisputeStatus.OPEN,
            "substatus": substatus,
            "reason": DisputeReason.CHECK_SUSPENDED,
            "evidence_files": [],
            "assigned_user_type": UserRole.TRADER,
            "assigned_user_id": order.trader_id,
        }

        async with self.session.begin_nested():
            dispute = await self.repository.create(dispute_data)
            await self.audit_log(
                action="open_dispute_premoderation",
                entity_type="dispute",
                entity_id=dispute.id,
                user_id=moderator_user_id,
                new_values={
                    "order_id": order.id,
                    "substatus": substatus.value,
                    "pre_dispute_status": pre_status.value,
                },
            )

        # Order → DISPUTED + reconcile through the single status funnel. The
        # premoderated order is active (RECEIPT_UPLOADED), so reconcile is a
        # no-op (collateral already in ESCROW); the funnel still normalises the
        # books correctly if a settled order is ever pulled here. We own the
        # audit above and the merchant callback below.
        from app.modules.orders.service import OrderService

        await OrderService(self.session).change_status(
            order, OrderStatus.DISPUTED, actor_id=moderator_user_id,
            audit_action=None, fire_callback=False,
        )

        from app.workers.tasks.trader_bot import notify_trader_new_dispute
        try:
            notify_trader_new_dispute.apply_async(args=[dispute.id], countdown=2)
        except Exception:  # pragma: no cover — broker best-effort
            pass

        self._notify_merchant_order_callback(order.id)
        return dispute

    async def request_proof_from_trader_group(
        self, order_uuid: str, telegram_group_id: int, kind: str
    ) -> Dispute:
        """Trader taps «Запросить видео/ПДФ» under the trader-bot «Новый чек»
        message. Group-authorises the order, opens (or advances) the
        ``check_suspended`` dispute (initiator=TRADER), and fans the merchant proof
        request out to the merchant's chat — gated by ``proof_request_notify_enabled``
        and routed by order source in the worker; the API webhook always fires via
        the dispute open."""
        from app.common.enums.receipt_moderations import ModerationDecision
        from app.modules.orders.service import OrderService

        decision_by_kind = {
            "video": ModerationDecision.REQUEST_VIDEO,
            "pdf": ModerationDecision.REQUEST_PDF,
        }
        decision = decision_by_kind.get(kind)
        if decision is None:
            raise ValidationException(
                f"Unsupported proof kind: {kind!r} (expected 'video' or 'pdf')"
            )

        order = await OrderService(self.session).get_order_for_trader_group(
            order_uuid, telegram_group_id
        )

        dispute = await self.open_dispute_from_premoderation(
            order, decision,
            initiator_type=UserRole.TRADER, initiator_id=order.trader_id,
        )
        if dispute is None:
            raise ValidationException(
                "Cannot request proof for this order (no assigned trader / already closed)"
            )

        from app.modules.receipts import effects as receipt_effects

        receipt_effects.enqueue_merchant_proof_request(order.id, decision.value)
        return dispute

    @staticmethod
    def _notify_merchant_order_callback(order_id: int) -> None:
        """Best-effort enqueue of the merchant order webhook (status change).

        Mirrors OrderService.complete_order / fail_order. Broker errors are
        swallowed — the dispute transition is already committed and the
        webhook is best-effort; a broker blip must not 500 the caller.
        """
        try:
            from app.workers.celery_app import celery_app
            celery_app.send_task(
                "app.workers.tasks.callbacks.send_order_callback",
                args=[order_id],
            )
        except Exception as exc:  # pragma: no cover — defensive
            import logging
            logging.getLogger(__name__).warning(
                "send_order_callback enqueue failed (order_id=%s): %s",
                order_id, exc,
            )

    async def get_merchant_dispute(self, merchant: Merchant, dispute_uuid: str) -> Dispute:
        dispute = await self.repository.get_by_uuid_and_merchant(dispute_uuid, merchant.id)
        if not dispute:
            raise NotFoundException(f"Dispute {dispute_uuid} not found")
        return dispute

    async def get_merchant_dispute_detail(
        self, merchant: Merchant, dispute_uuid: str
    ) -> "DisputeMerchantResponse":
        """Return dispute detail for the merchant view (restricted schema),
        enriched with the merchant's own order snapshot."""
        from app.modules.disputes.schemas import DisputeMerchantResponse

        dispute = await self.get_merchant_dispute(merchant, dispute_uuid)

        resp = DisputeMerchantResponse.model_validate(dispute, from_attributes=True)

        order = await self.order_repository.get(dispute.order_id)
        if order:
            resp.order_uuid = str(order.uuid) if order.uuid else None
            resp.order_external_id = order.external_id
            resp.order_amount = float(order.amount) if order.amount is not None else None
            resp.order_payment_method = (
                order.payment_method.value
                if hasattr(order.payment_method, "value") else order.payment_method
            )

        return resp

    async def add_merchant_evidence(
        self,
        merchant: Merchant,
        dispute_uuid: str,
        attachment: UploadFile,
        *,
        uploaded_by: ReceiptUploader,
    ) -> "DisputeMerchantResponse":
        """Merchant attaches ANOTHER receipt to their own already-OPEN dispute
        (the post-open re-upload — e.g. to fulfil a ``pdf_requested`` /
        ``video_requested`` proof request). Mirrors the dispute-bot path:
        ``confirm_order`` links the receipt to the dispute (``dispute_id``) and
        runs premoderation, which surfaces it back to the trader. ``uploaded_by``
        carries the channel (HMAC API vs cabinet). Scoped to the merchant's own
        disputes; works only while the dispute is open."""
        dispute = await self.get_merchant_dispute(merchant, dispute_uuid)
        if dispute.status != DisputeStatus.OPEN:
            raise ValidationException("Dispute is already closed")

        order = await self.order_repository.get(dispute.order_id)
        if order is None:
            raise NotFoundException(f"Order for dispute {dispute_uuid} not found")

        # Reuse the canonical receipt-upload path (validate → store → premoderate
        # → effects); ``dispute_id`` links the receipt to the appeal. Lazy import
        # breaks the orders↔disputes cycle.
        from app.modules.orders.service import OrderService

        await OrderService(self.session).confirm_order(
            merchant=merchant,
            attachment=attachment,
            order_id=str(order.uuid),
            uploaded_by=uploaded_by,
            dispute_id=dispute.id,
        )
        # Re-mirror evidence_files so evidence_count reflects the new receipt —
        # same as the create-endpoint re-attach (confirm_order links the receipt
        # but doesn't touch the dispute's evidence_files itself).
        await self._mirror_dispute_evidence_files(dispute.id)
        return await self.get_merchant_dispute_detail(merchant, dispute_uuid)

    async def resolve_dispute(self, admin_id: int, dispute_id: int, resolution_text: str) -> Dispute:
        """Resolve a dispute in the merchant's favour (admin) → order SUCCESS."""
        dispute = await self.repository.get(dispute_id)
        if not dispute:
            raise NotFoundException(f"Dispute {dispute_id} not found")

        # Lock the dispute row so a concurrent resolve/reject serialises here and
        # the loser sees the closed status instead of double-writing + double-auditing.
        locked_status = await self.repository.lock_status(dispute.id)
        if locked_status in (DisputeStatus.RESOLVED, DisputeStatus.REJECTED):
            raise ValidationException("Dispute is already closed")

        return await self._apply_resolve_merchant_favor(
            dispute,
            resolved_by_type=UserRole.ADMIN,
            resolved_by_id=admin_id,
            resolution_text=resolution_text,
        )

    async def _apply_resolve_merchant_favor(
        self,
        dispute: Dispute,
        *,
        resolved_by_type: UserRole,
        resolved_by_id: int,
        resolution_text: str,
    ) -> Dispute:
        """Resolve an already-fetched, OPEN dispute in the merchant's favour →
        order becomes SUCCESS.

        Shared by the admin (`resolve_dispute`) and the trader's self-service
        accept (`trader_accept_dispute`) so the money path is identical no matter
        who concedes. The order sits in DISPUTED with the trader's collateral
        frozen in ESCROW (reconcile_for_dispute normalised it on open), so this
        is just the ordinary completion flow regardless of the pre-dispute status.
        """
        order = await self.order_repository.get(dispute.order_id)

        from app.modules.users.models import User
        trader = (
            await self.session.get(User, order.trader_id)
            if order.trader_id
            else None
        )
        if trader is None:
            raise ValidationException(
                "Cannot resolve dispute: order has no assigned trader "
                "(order was never processed)."
            )

        async with self.session.begin_nested():
            dispute = await self.repository.update(
                dispute.id,
                {
                    "status": DisputeStatus.RESOLVED,
                    "resolution_text": resolution_text,
                    "resolved_by_type": resolved_by_type,
                    "resolved_by_id": resolved_by_id,
                    "resolved_at": utcnow(),
                }
            )
            await self.audit_log(
                action="resolve_dispute",
                entity_type="dispute",
                entity_id=dispute.id,
                user_id=resolved_by_id,
                new_values={
                    "status": DisputeStatus.RESOLVED.value,
                    "resolution_text": resolution_text,
                    "resolved_by_type": resolved_by_type.value,
                },
            )

        # Order → SUCCESS via the single transition funnel: complete_order settles
        # EVERYTHING fresh (escrow, fee, trader + teamlead rewards, turnover).
        # Opening the dispute already reversed any prior settlement, so this is a
        # clean re-settle at the order's current amount — no special-casing.
        from app.modules.orders.service import OrderService

        await OrderService(self.session).change_status(
            order, OrderStatus.SUCCESS, actor_id=resolved_by_id,
            audit_action=None, fire_callback=False,
        )

        self._notify_merchant_order_callback(order.id)

        return dispute

    async def reject_dispute(self, admin_id: int, dispute_id: int, resolution_text: str) -> Dispute:
        """Reject a dispute (favour of trader / client) → order becomes FAILED.

        The frozen collateral is released back to the trader — the ordinary
        cancel flow — regardless of what the order's pre-dispute status was.
        """
        dispute = await self.repository.get(dispute_id)
        if not dispute:
            raise NotFoundException(f"Dispute {dispute_id} not found")

        # Lock the dispute row so a concurrent resolve/reject serialises here.
        locked_status = await self.repository.lock_status(dispute.id)
        if locked_status in (DisputeStatus.RESOLVED, DisputeStatus.REJECTED):
            raise ValidationException("Dispute is already closed")

        return await self._apply_reject(
            dispute,
            resolved_by_type=UserRole.ADMIN,
            resolved_by_id=admin_id,
            resolution_text=resolution_text,
        )

    async def _apply_reject(
        self,
        dispute: Dispute,
        *,
        resolved_by_type: UserRole,
        resolved_by_id: int,
        resolution_text: str,
    ) -> Dispute:
        """Reject an already-fetched, locked, OPEN dispute (favour of trader /
        client) → order FAILED, frozen collateral released to the trader.

        Shared by the admin (`reject_dispute`) and the trader's self-service
        decline (`trader_reject_dispute`) so the money path is identical no matter
        who declines. Opening the dispute already normalised the books, so FAILED
        just releases the collateral."""
        order = await self.order_repository.get(dispute.order_id)

        from app.modules.users.models import User
        trader = (
            await self.session.get(User, order.trader_id)
            if order.trader_id
            else None
        )
        if trader is None:
            raise ValidationException(
                "Cannot reject dispute: order has no assigned trader "
                "(order was never processed)."
            )

        async with self.session.begin_nested():
            dispute = await self.repository.update(
                dispute.id,
                {
                    "status": DisputeStatus.REJECTED,
                    "resolution_text": resolution_text,
                    "resolved_by_type": resolved_by_type,
                    "resolved_by_id": resolved_by_id,
                    "resolved_at": utcnow(),
                }
            )
            await self.audit_log(
                action="reject_dispute",
                entity_type="dispute",
                entity_id=dispute.id,
                user_id=resolved_by_id,
                new_values={
                    "status": DisputeStatus.REJECTED.value,
                    "resolution_text": resolution_text,
                    "resolved_by_type": resolved_by_type.value,
                },
            )

        # Order → FAILED via the single transition funnel (frozen collateral
        # released back to the trader).
        from app.modules.orders.service import OrderService

        order = await OrderService(self.session).change_status(
            order, OrderStatus.FAILED, actor_id=resolved_by_id, reason=resolution_text,
            audit_action=None, fire_callback=False,
        )
        # No teamlead/turnover reversal here: opening the dispute already
        # normalised the books (reconcile_for_dispute reversed the settlement,
        # incl. teamlead rewards + turnover, when the order was pre-SUCCESS), so
        # by now the collateral is simply frozen and FAILED just releases it.

        self._notify_merchant_order_callback(order.id)

        return dispute

    # ── Trader methods ─────────────────────────────────────────

    async def list_trader_disputes(
        self,
        trader_id: int,
        *,
        status: Optional[str] = None,
        reason: Optional[str] = None,
        payment_method: Optional[str] = None,
        id_search: Optional[str] = None,
        amount_from: Optional[float] = None,
        amount_to: Optional[float] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> List["DisputeResponse"]:
        """Return enriched disputes for the given trader with order info."""
        from app.modules.disputes.schemas import DisputeResponse

        disputes = await self.repository.list_for_trader(
            trader_id,
            status=status,
            reason=reason,
            payment_method=payment_method,
            id_search=id_search,
            amount_from=amount_from,
            amount_to=amount_to,
            skip=skip,
            limit=limit,
        )
        if not disputes:
            return []

        from sqlalchemy import select as _sa_select

        order_ids = [d.order_id for d in disputes]
        orders_result = await self.session.execute(
            _sa_select(Order).where(Order.id.in_(order_ids))
        )
        orders_by_id = {o.id: o for o in orders_result.scalars().all()}

        responses: List[DisputeResponse] = []
        for d in disputes:
            resp = DisputeResponse.model_validate(d, from_attributes=True)
            order = orders_by_id.get(d.order_id)
            if order:
                resp.order_uuid = str(order.uuid) if order.uuid else None
                resp.order_external_id = order.external_id
                resp.order_amount = float(order.amount) if order.amount is not None else None
                resp.order_payment_method = (
                    order.payment_method.value if hasattr(order.payment_method, "value") else order.payment_method
                )
            responses.append(resp)
        return responses

    async def get_trader_dispute(self, trader_id: int, dispute_uuid: str) -> Dispute:
        dispute = await self.repository.get_by_uuid_and_trader(dispute_uuid, trader_id)
        if not dispute:
            raise NotFoundException(f"Dispute {dispute_uuid} not found")
        return dispute

    async def get_trader_dispute_detail(
        self, trader_id: int, dispute_uuid: str
    ) -> "DisputeResponse":
        """Return dispute detail for trader, enriched with order info."""
        from app.modules.disputes.schemas import DisputeResponse

        dispute = await self.get_trader_dispute(trader_id, dispute_uuid)
        order = await self.order_repository.get(dispute.order_id)

        resp = DisputeResponse.model_validate(dispute, from_attributes=True)
        if order:
            resp.order_uuid = str(order.uuid) if order.uuid else None
            resp.order_external_id = order.external_id
            resp.order_amount = float(order.amount) if order.amount is not None else None
            resp.order_payment_method = (
                order.payment_method.value if hasattr(order.payment_method, "value") else order.payment_method
            )
        return resp

    async def trader_accept_dispute(self, trader_id: int, dispute_uuid: str) -> Dispute:
        """Trader concedes the dispute → resolved in the merchant's favour
        (order SUCCESS). Self-service for the obvious cases.

        To push back instead, the trader rejects (`trader_reject_dispute` → order
        FAILED) or asks for stronger proof (`trader_request_proof`). Scoped to the
        trader's own orders.
        """
        dispute = await self.get_trader_dispute(trader_id, dispute_uuid)

        locked_status = await self.repository.lock_status(dispute.id)
        if locked_status in (DisputeStatus.RESOLVED, DisputeStatus.REJECTED):
            raise ValidationException("Dispute is already closed")

        return await self._apply_resolve_merchant_favor(
            dispute,
            resolved_by_type=UserRole.TRADER,
            resolved_by_id=trader_id,
            resolution_text=TRADER_ACCEPT_RESOLUTION_TEXT,
        )

    async def trader_reject_dispute(self, trader_id: int, dispute_uuid: str) -> Dispute:
        """Trader declines the dispute → rejected in their OWN favour (order FAILED,
        frozen collateral released back to them). The trader's self-service
        DECISION — replaces the old «contest» escalation. The admin keeps
        resolve/reject as an override. Scoped to the trader's own orders."""
        dispute = await self.get_trader_dispute(trader_id, dispute_uuid)

        locked_status = await self.repository.lock_status(dispute.id)
        if locked_status in (DisputeStatus.RESOLVED, DisputeStatus.REJECTED):
            raise ValidationException("Dispute is already closed")

        return await self._apply_reject(
            dispute,
            resolved_by_type=UserRole.TRADER,
            resolved_by_id=trader_id,
            resolution_text=TRADER_REJECT_RESOLUTION_TEXT,
        )

    async def trader_request_proof(
        self, trader_id: int, dispute_uuid: str, kind: str
    ) -> Dispute:
        """Trader asks the merchant for stronger proof (video / PDF) instead of
        deciding now. The dispute stays OPEN with the matching substatus and the
        merchant is nudged (callback + merchant-bot) for the new proof; once the
        merchant re-submits it, premoderation re-surfaces the dispute to the
        trader (the request→proof→premoderation→trader loop). Scoped to the
        trader's own orders."""
        from app.common.enums.disputes import DisputeSubstatus
        from app.common.enums.receipt_moderations import ModerationDecision

        request_map = {
            "video": (DisputeSubstatus.VIDEO_REQUESTED, ModerationDecision.REQUEST_VIDEO),
            "pdf": (DisputeSubstatus.PDF_REQUESTED, ModerationDecision.REQUEST_PDF),
        }
        mapped = request_map.get(kind)
        if mapped is None:
            raise ValidationException(
                f"Unsupported proof kind: {kind!r} (expected 'video' or 'pdf')"
            )
        substatus, decision = mapped

        dispute = await self.get_trader_dispute(trader_id, dispute_uuid)

        locked_status = await self.repository.lock_status(dispute.id)
        if locked_status in (DisputeStatus.RESOLVED, DisputeStatus.REJECTED):
            raise ValidationException("Dispute is already closed")

        async with self.session.begin_nested():
            dispute = await self.repository.update(dispute.id, {"substatus": substatus})
            await self.audit_log(
                action="trader_request_proof",
                entity_type="dispute",
                entity_id=dispute.id,
                user_id=trader_id,
                new_values={"substatus": substatus.value},
            )

        # Nudge the merchant for the requested proof on every channel:
        #  • merchant-bot DM (the premoderation proof-request channel), and
        #  • the order webhook — the dispute callback now carries the substatus
        #    (pdf_requested / video_requested), same as the premoderation path
        #    (open_dispute_from_premoderation) does on its requests.
        # Lazy import — the disputes↔receipts cycle is broken by importing here.
        from app.modules.receipts import effects as receipt_effects

        receipt_effects.enqueue_merchant_proof_request(dispute.order_id, decision.value)
        self._notify_merchant_order_callback(dispute.order_id)

        return dispute

    # ── Evidence (dispute-linked receipts) ─────────────────────
    #
    # A dispute's evidence files are stored as ordinary receipts carrying the
    # ``dispute_id`` (uploaded through ReceiptService.upload on open). These
    # helpers list them / resolve one for download, role-scoped: the caller
    # first resolves+authorises the dispute (admin by id, trader/merchant by
    # uuid within their own scope), then reads the linked receipts. Traders see
    # only premoderation-visible receipts — same rule as order receipts — so an
    # un-reviewed upload never leaks before moderation.

    async def _list_evidence_receipts(
        self, dispute_id: int, *, visible_to_trader_only: bool = False
    ) -> List["Receipt"]:
        from app.common.enums.receipt_moderations import is_receipt_visible_to_trader
        from app.modules.receipts.service import ReceiptService

        rows = await ReceiptService(self.session).list_for_dispute(dispute_id)
        if visible_to_trader_only:
            rows = [r for r in rows if is_receipt_visible_to_trader(r.moderation_status)]
        return rows

    async def _resolve_evidence_receipt(
        self, dispute_id: int, receipt_uuid: str, *, visible_to_trader_only: bool = False
    ) -> "Receipt":
        from app.common.enums.receipt_moderations import is_receipt_visible_to_trader
        from app.modules.receipts.service import ReceiptService

        receipt = await ReceiptService(self.session).get_by_uuid(receipt_uuid)
        # Uniform 404 for not-found / wrong-dispute / not-yet-visible — never
        # leak the existence of evidence the caller isn't allowed to see.
        if not receipt or receipt.dispute_id != dispute_id:
            raise NotFoundException("Evidence not found")
        if visible_to_trader_only and not is_receipt_visible_to_trader(receipt.moderation_status):
            raise NotFoundException("Evidence not found")
        return receipt

    # Admin — full access by numeric dispute id.
    async def list_evidence_admin(self, dispute_id: int) -> List["Receipt"]:
        if not await self.repository.get(dispute_id):
            raise NotFoundException(f"Dispute {dispute_id} not found")
        return await self._list_evidence_receipts(dispute_id)

    async def get_evidence_receipt_admin(self, dispute_id: int, receipt_uuid: str) -> "Receipt":
        if not await self.repository.get(dispute_id):
            raise NotFoundException(f"Dispute {dispute_id} not found")
        return await self._resolve_evidence_receipt(dispute_id, receipt_uuid)

    # Trader — own disputes only, premoderation-visible receipts only.
    async def list_evidence_trader(self, trader_id: int, dispute_uuid: str) -> List["Receipt"]:
        dispute = await self.get_trader_dispute(trader_id, dispute_uuid)
        return await self._list_evidence_receipts(dispute.id, visible_to_trader_only=True)

    async def get_evidence_receipt_trader(
        self, trader_id: int, dispute_uuid: str, receipt_uuid: str
    ) -> "Receipt":
        dispute = await self.get_trader_dispute(trader_id, dispute_uuid)
        return await self._resolve_evidence_receipt(
            dispute.id, receipt_uuid, visible_to_trader_only=True
        )

    # Merchant — own disputes only.
    async def list_evidence_merchant(self, merchant: Merchant, dispute_uuid: str) -> List["Receipt"]:
        dispute = await self.get_merchant_dispute(merchant, dispute_uuid)
        return await self._list_evidence_receipts(dispute.id)

    async def get_evidence_receipt_merchant(
        self, merchant: Merchant, dispute_uuid: str, receipt_uuid: str
    ) -> "Receipt":
        dispute = await self.get_merchant_dispute(merchant, dispute_uuid)
        return await self._resolve_evidence_receipt(dispute.id, receipt_uuid)
