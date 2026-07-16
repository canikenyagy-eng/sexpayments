"""Shared moderation-decision orchestration.

A SINGLE path that applies a human moderation decision (first-wins) AND fans out
the deferred side effects, used by BOTH entry points so they can never drift:

  * the support-bot inline-keyboard callback
    (``POST /api/bot/v1/support/orders/{uuid}/moderate``)
  * the admin web UI
    (``POST /api/v1/receipt-moderations/{order_id}/decide``)

ACCEPT releases the work that premoderation gated (trader notify + auto fraud
check + cascade forward); REQUEST_PDF / REQUEST_VIDEO opens/advances the dispute
and nudges the merchant for better proof.

Kept OUT of the pure ``ReceiptModerationService`` (which stays side-effect-free
and unit-testable) — this coordinator is the thin glue that composes the service
with the effects + dispute open.
"""
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.receipt_moderations import ModerationDecision
from app.core.logging import get_logger
from app.modules.orders.models import Order
from app.modules.receipts import effects as receipt_effects
from app.modules.receipts.models import ReceiptModeration
from app.modules.receipts.moderation.service import ReceiptModerationService

logger = get_logger(__name__)


async def apply_moderation_decision(
    *,
    session: AsyncSession,
    order: Order,
    decision: ModerationDecision,
    moderator_tg_id: Optional[int] = None,
    moderator_username: Optional[str] = None,
    message_id: Optional[int] = None,
) -> ReceiptModeration:
    """Apply ``decision`` to ``order``'s pending moderation (first-wins) and run
    the matching side effects.

    Raises ``ModerationAlreadyDecidedError`` (HTTP 400 conflict) when the cycle
    is no longer pending — i.e. another admin / the bot already decided it.
    Side-effect enqueue failures are swallowed inside ``receipt_effects`` (the
    decision is already persisted), and the dispute open is best-effort.
    """
    row = await ReceiptModerationService(session).apply_decision(
        order=order,
        decision=decision,
        moderator_tg_id=moderator_tg_id,
        moderator_username=moderator_username,
        message_id=message_id,
    )

    if decision == ModerationDecision.ACCEPT:
        receipt_effects.fire_receipt_approved(
            order.id, notify_trader=True, notify_countdown=1
        )
    else:
        # Receipt rejected → open/advance the dispute, then nudge the merchant
        # for better proof. Dispute open is lazy-imported (avoids the
        # disputes↔receipts import cycle) and best-effort: the decision is
        # already persisted, so a dispute hiccup must not fail the request.
        dispute = None
        try:
            from app.modules.disputes.service import DisputeService

            dispute = await DisputeService(session).open_dispute_from_premoderation(
                order=order, decision=decision,
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "open_dispute_from_premoderation failed",
                order_id=order.id, error=str(exc),
            )
        # Nudge the merchant for proof ONLY when a dispute is actually OPEN to
        # receive it — never on a closed/never-opened one (a late decision on a
        # already-resolved dispute must not notify on a settled order).
        if dispute is not None:
            receipt_effects.enqueue_merchant_proof_request(order.id, decision.value)

    return row
