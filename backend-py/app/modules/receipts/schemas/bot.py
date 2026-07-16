"""Bot-facing receipt-moderation schemas (support-bot ⇄ backend channel).

Self-contained — никаких импортов из sibling-схем модуля (claude.md).
"""
from typing import Optional

from pydantic import BaseModel, Field

from app.common.enums.receipt_moderations import ModerationDecision, ModerationStatus


class SupportBotModerationDecisionRequest(BaseModel):
    """Payload posted by support-bot when an admin clicks one of the buttons."""

    decision: ModerationDecision
    moderator_tg_id: Optional[int] = Field(default=None, description="Telegram user id of the admin")
    moderator_username: Optional[str] = Field(default=None, max_length=64)
    message_id: Optional[int] = Field(default=None, description="Telegram message_id (used for edit, optional)")


class SupportBotModerationResponse(BaseModel):
    """Returned to support-bot after a decision is recorded."""

    order_uuid: str
    moderation_status: ModerationStatus
    decision: ModerationDecision


class SupportBotMessageAck(BaseModel):
    """Payload the support-bot posts back to the backend after delivering
    the receipt card — contains the Telegram ``message_id`` of the inline
    keyboard message, which we back-fill into the moderation row so a
    later callback can edit that exact message.
    """

    message_id: int
