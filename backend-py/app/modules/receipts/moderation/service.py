"""Receipt moderation — the single owner of a receipt's moderation lifecycle.

Two legs, ONE service:

  * the DECISION engine — ``evaluate()`` runs the policy + the ordered checker
    registry and returns one of AUTO_APPROVE / AUTO_REJECT / ESCALATE_HUMAN.
    The seam where a future model plugs in lives in ``checkers.py``.

  * the HUMAN-REVIEW state machine — when ``evaluate()`` escalates, ``create_pending``
    opens the review row + flips the order to PENDING, ``set_message_id``
    back-fills the bot's Telegram message id, and ``apply_decision`` finalises
    the admin's click (first-wins under concurrency, mirrored onto the order's
    pending receipts).

With no auto-checker enabled today, ``evaluate`` reproduces the historical
behaviour exactly: review-required → ESCALATE_HUMAN; otherwise AUTO_APPROVE.

Side effects (notify trader / notify merchant) are NOT triggered here — they
live in the callers (upload path / support-bot endpoint), keeping this service
pure and directly testable without a Celery broker.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.receipt_moderations import (
    DECISION_TO_STATUS,
    ModerationDecision,
    ModerationStatus,
)
from app.common.types import utcnow
from app.modules.base.service import BaseService
from app.modules.orders.models import Order
from app.modules.receipts.exceptions import (
    ModerationAlreadyDecidedError,
    ModerationRowNotFoundError,
)
from app.modules.receipts.models import ReceiptModeration
from app.modules.receipts.moderation.checkers import (
    _AUTO_CHECKERS,
    CheckDecision,
    CheckVerdict,
)
from app.modules.receipts.moderation.policy import ReceiptModerationPolicy
from app.modules.receipts.repository import ReceiptModerationRepository


class ModerationOutcome(str, Enum):
    AUTO_APPROVE = "auto_approve"
    AUTO_REJECT = "auto_reject"
    ESCALATE_HUMAN = "escalate_human"


@dataclass
class ModerationResult:
    outcome: ModerationOutcome
    support_chat_id: int = 0
    reject_reason: str = ""
    verdicts: List[CheckVerdict] = field(default_factory=list)


class ReceiptModerationService(BaseService):
    """The receipts module's single moderation entry point — decision engine
    plus the human-review lifecycle."""

    # Confidence at/above which an automatic APPROVE/REJECT is trusted without
    # a human. Tunable later via PlatformSetting per-checker.
    AUTO_CONFIDENCE_THRESHOLD = 0.99

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repo = ReceiptModerationRepository(session)

    # ── decision engine ──────────────────────────────────────────────────
    async def evaluate(self, order, merchant) -> ModerationResult:
        """Decide what happens to a freshly uploaded receipt: run the policy,
        then the ordered automatic-checker registry, with human review as the
        escalation fallback."""
        resolution = await ReceiptModerationPolicy(self.session).resolve(order, merchant)
        if not resolution.review_required:
            # Premoderation off (or misconfigured chat) → straight to approve.
            return ModerationResult(outcome=ModerationOutcome.AUTO_APPROVE)

        verdicts: List[CheckVerdict] = []
        for checker in _AUTO_CHECKERS:
            if not getattr(checker, "enabled", False):
                continue
            verdict = await checker.check(order=order, merchant=merchant, session=self.session)
            verdicts.append(verdict)
            if verdict.decision == CheckDecision.REJECT and verdict.confidence >= self.AUTO_CONFIDENCE_THRESHOLD:
                return ModerationResult(
                    outcome=ModerationOutcome.AUTO_REJECT,
                    reject_reason=verdict.reason, verdicts=verdicts,
                )

        # Confident unanimous approve from the automatic checkers → skip human.
        approvals = [
            v for v in verdicts
            if v.decision == CheckDecision.APPROVE and v.confidence >= self.AUTO_CONFIDENCE_THRESHOLD
        ]
        if verdicts and len(approvals) == len(verdicts):
            return ModerationResult(outcome=ModerationOutcome.AUTO_APPROVE, verdicts=verdicts)

        # Anything uncertain (incl. the no-enabled-checker case) → human review.
        return ModerationResult(
            outcome=ModerationOutcome.ESCALATE_HUMAN,
            support_chat_id=resolution.support_chat_id, verdicts=verdicts,
        )

    # ── human-review state machine ───────────────────────────────────────
    async def create_pending(self, order: Order, chat_id: int) -> ReceiptModeration:
        """Open a moderation cycle for the order.

        Idempotent on the order side: also sets ``order.moderation_status`` to
        PENDING in the same flush so the gating check survives a retried Celery
        delivery.
        """
        async with self.session.begin_nested():
            row = ReceiptModeration(order_id=order.id, chat_id=chat_id)
            self.session.add(row)
            order.moderation_status = ModerationStatus.PENDING
            self.session.add(order)
            await self.session.flush()
            await self.session.refresh(row)

            await self.audit_log(
                action="open_receipt_moderation",
                entity_type="order",
                entity_id=order.id,
                new_values={
                    "moderation_id": row.id,
                    "chat_id": chat_id,
                    "moderation_status": ModerationStatus.PENDING.value,
                },
            )
        return row

    async def set_message_id(self, moderation_id: int, message_id: int) -> None:
        """Back-fill the bot's message_id after delivery."""
        row = await self.repo.get(moderation_id)
        if row is None:
            return
        row.message_id = message_id
        self.session.add(row)
        await self.session.flush()

    async def apply_decision(
        self,
        order: Order,
        decision: ModerationDecision,
        moderator_tg_id: Optional[int],
        moderator_username: Optional[str],
        message_id: Optional[int],
    ) -> ReceiptModeration:
        """First-wins finalisation of the latest moderation cycle.

        Atomic UPDATE … WHERE moderation_status='pending' cuts the race when two
        admins click at the same moment: only the row whose precondition still
        holds is updated. ``ModerationAlreadyDecidedError`` is raised when zero
        rows were affected.
        """
        new_status = DECISION_TO_STATUS[decision]
        now = utcnow()

        async with self.session.begin_nested():
            result = await self.session.execute(
                update(Order)
                .where(
                    Order.id == order.id,
                    Order.moderation_status == ModerationStatus.PENDING,
                )
                .values(moderation_status=new_status)
            )

            if result.rowcount == 0:
                raise ModerationAlreadyDecidedError()

            # Mirror the decision onto this order's pending receipts so the
            # per-receipt trader-visibility gate stays in sync: ACCEPT →
            # APPROVED (visible); pdf/video_requested → stays hidden. Batched
            # per order (all receipts awaiting this cycle move together).
            from app.modules.receipts.models import Receipt

            await self.session.execute(
                update(Receipt)
                .where(
                    Receipt.order_id == order.id,
                    Receipt.moderation_status == ModerationStatus.PENDING,
                )
                .values(moderation_status=new_status)
            )

            row = await self.repo.get_latest_for_order(order.id)
            if row is None:
                # Status was 'pending' but no moderation row exists — shouldn't
                # happen under normal flow (create_pending always writes a row),
                # but defend against manual DB tinkering anyway.
                raise ModerationRowNotFoundError(order.id)

            row.decision = decision
            row.moderator_tg_id = moderator_tg_id
            row.moderator_username = moderator_username
            row.decided_at = now
            if message_id is not None:
                row.message_id = message_id
            self.session.add(row)

            # Keep the order's in-memory copy in sync with the UPDATE we just
            # issued so callers can observe the new status without a refetch.
            order.moderation_status = new_status

            await self.session.flush()
            await self.session.refresh(row)

            await self.audit_log(
                action="moderate_receipt",
                entity_type="order",
                entity_id=order.id,
                user_id=moderator_tg_id,
                new_values={
                    "decision": decision.value,
                    "moderation_status": new_status.value,
                    "moderator_username": moderator_username,
                    "message_id": message_id,
                },
            )

        return row
