"""Receipts schemas package.

``read`` holds the cross-role receipt list item; ``admin`` / ``bot`` hold the
role-split receipt-moderation schemas. Re-exported here for backward-compat —
new code SHOULD import from the role-specific file (per CLAUDE.md).
"""
from app.modules.receipts.schemas.admin import (  # noqa: F401
    AdminReceiptModerationItem,
    AdminReceiptModerationListResponse,
)
from app.modules.receipts.schemas.bot import (  # noqa: F401
    SupportBotMessageAck,
    SupportBotModerationDecisionRequest,
    SupportBotModerationResponse,
)
from app.modules.receipts.schemas.read import ReceiptItem  # noqa: F401

__all__ = [
    "ReceiptItem",
    "AdminReceiptModerationItem",
    "AdminReceiptModerationListResponse",
    "SupportBotModerationDecisionRequest",
    "SupportBotModerationResponse",
    "SupportBotMessageAck",
]
