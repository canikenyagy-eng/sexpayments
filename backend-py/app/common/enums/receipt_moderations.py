"""Receipt-moderation enums (support-bot premoderation flow).

Lifecycle:
  Merchant uploads receipt → if premoderation flag is ON → moderation row
  is created in PENDING → support-bot delivers the receipt to the admin chat
  with three inline buttons → admin clicks one → backend writes the
  decision (first-wins) and fires the matching side effect:

    ACCEPT        → notify trader (existing trader-bot flow)
    REQUEST_PDF   → notify merchant via merchant-notify-bot ("прикрепите PDF")
    REQUEST_VIDEO → notify merchant via merchant-notify-bot ("прикрепите видео")

When premoderation is OFF the order keeps ``ModerationStatus.NONE`` and
the trader is notified immediately as before — no moderation row written.
"""
from enum import Enum


class ModerationStatus(str, Enum):
    """Per-order moderation state for the support-bot pre-trader review."""

    NONE = "none"                       # premoderation not in scope for this order
    PENDING = "pending"                 # receipt sent to support-bot, awaiting click
    APPROVED = "approved"               # admin clicked «Принять»
    PDF_REQUESTED = "pdf_requested"     # admin clicked «Запросить ПДФ»
    VIDEO_REQUESTED = "video_requested" # admin clicked «Запросить Видео»


class ModerationDecision(str, Enum):
    """One of three actions an admin can pick in the support chat."""

    ACCEPT = "accept"
    REQUEST_PDF = "request_pdf"
    REQUEST_VIDEO = "request_video"


# Decision → resulting order moderation_status. Mirrored both as the
# atomic UPDATE target in ReceiptModerationService and as the discriminator
# for downstream side effects (notify trader vs notify merchant).
DECISION_TO_STATUS: dict[ModerationDecision, ModerationStatus] = {
    ModerationDecision.ACCEPT: ModerationStatus.APPROVED,
    ModerationDecision.REQUEST_PDF: ModerationStatus.PDF_REQUESTED,
    ModerationDecision.REQUEST_VIDEO: ModerationStatus.VIDEO_REQUESTED,
}


# Terminal moderation statuses — once an order reaches any of these, the
# moderation cycle is closed. Used by the service to reject second clicks.
FINAL_MODERATION_STATUSES: frozenset[ModerationStatus] = frozenset({
    ModerationStatus.APPROVED,
    ModerationStatus.PDF_REQUESTED,
    ModerationStatus.VIDEO_REQUESTED,
})


# Single source of truth for "can the trader see the uploaded receipt?".
# Only an order with no premoderation gate (``NONE``) or one an admin has
# explicitly accepted (``APPROVED``) is visible. PDF/VIDEO requests mean the
# current upload was rejected and we're waiting for a new one — the trader
# must see the order as if no receipt was uploaded at all.
_RECEIPT_VISIBLE_TO_TRADER: frozenset[ModerationStatus] = frozenset({
    ModerationStatus.NONE,
    ModerationStatus.APPROVED,
})


def is_receipt_visible_to_trader(moderation_status: ModerationStatus) -> bool:
    """Predicate used by every trader-side surface (response schema,
    download endpoint, future notifications) to project a coherent view:
    until admin clicks «Принять», the order looks to the trader exactly
    like one whose receipt has not been uploaded yet (status pending,
    no file, no timestamps).
    """
    return moderation_status in _RECEIPT_VISIBLE_TO_TRADER
