"""Admin-facing receipt-moderation schemas.

Self-contained — никаких импортов из sibling-схем модуля (claude.md).
Exposes the full row plus enriched fields from the joined order so the
admin UI can render the history table without a second request.
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from app.common.enums.receipt_moderations import ModerationDecision, ModerationStatus


class AdminReceiptModerationItem(BaseModel):
    """One row of moderation history, enriched with order/merchant fields."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    order_id: int
    order_uuid: str
    order_external_id: str

    merchant_id: int
    merchant_name: Optional[str] = None
    trader_id: Optional[int] = None
    trader_username: Optional[str] = None

    moderation_status: ModerationStatus
    has_receipt: bool = False

    chat_id: int
    message_id: Optional[int] = None
    decision: Optional[ModerationDecision] = None
    moderator_tg_id: Optional[int] = None
    moderator_username: Optional[str] = None

    created_at: datetime
    decided_at: Optional[datetime] = None


class AdminReceiptModerationListResponse(BaseModel):
    items: List[AdminReceiptModerationItem]
    total: int
    skip: int
    limit: int


class AdminModerationDecisionRequest(BaseModel):
    """Body for the admin web-UI moderation action — the same three decisions as
    the support-bot inline keyboard (accept / request_pdf / request_video)."""

    decision: ModerationDecision
