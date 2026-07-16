"""Receipt moderation — one cohesive component of the receipts module.

``ReceiptModerationService`` owns the whole lifecycle (decision engine +
human review). ``checkers`` is the automatic-verification seam (model plug-in
point); ``policy`` answers whether/where a receipt needs review.
"""
from app.modules.receipts.moderation.checkers import (  # noqa: F401
    CheckDecision,
    CheckVerdict,
    ModelReceiptChecker,
    ReceiptChecker,
    register_checker,
)
from app.modules.receipts.moderation.policy import (  # noqa: F401
    ModerationResolution,
    ReceiptModerationPolicy,
)
from app.modules.receipts.moderation.service import (  # noqa: F401
    ModerationOutcome,
    ModerationResult,
    ReceiptModerationService,
)

__all__ = [
    "ReceiptModerationService",
    "ModerationOutcome",
    "ModerationResult",
    "ReceiptModerationPolicy",
    "ModerationResolution",
    "ReceiptChecker",
    "ModelReceiptChecker",
    "CheckDecision",
    "CheckVerdict",
    "register_checker",
]
