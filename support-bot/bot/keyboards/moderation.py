"""Inline-keyboard helpers for the moderation card.

Callback data layout:  ``mod:<order_uuid>:<decision>``
Where ``decision`` is one of: ``accept``, ``pdf``, ``video``. The bot
maps ``pdf`` / ``video`` to ``request_pdf`` / ``request_video`` before
posting to the backend — short tokens keep the callback under Telegram's
64-byte limit even for the longest UUIDs.
"""
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


CALLBACK_PREFIX = "mod"
NOOP_CALLBACK = "mod_noop"

DECISION_ACCEPT = "accept"
DECISION_PDF = "pdf"
DECISION_VIDEO = "video"

LABELS: dict[str, str] = {
    DECISION_ACCEPT: "Принять",
    DECISION_PDF: "Запросить чек ПДФ",
    DECISION_VIDEO: "Запросить Видео",
}


def _cb(order_uuid: str, decision: str) -> str:
    return f"{CALLBACK_PREFIX}:{order_uuid}:{decision}"


def parse_callback(data: str) -> tuple[str, str] | None:
    """Returns ``(order_uuid, decision)`` or ``None`` if the prefix/format
    doesn't match — handlers use the ``None`` branch to politely answer
    the callback without raising.
    """
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        return None
    return parts[1], parts[2]


def build_moderation_keyboard(order_uuid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=LABELS[DECISION_ACCEPT], callback_data=_cb(order_uuid, DECISION_ACCEPT))],
            [InlineKeyboardButton(text=LABELS[DECISION_PDF], callback_data=_cb(order_uuid, DECISION_PDF))],
            [InlineKeyboardButton(text=LABELS[DECISION_VIDEO], callback_data=_cb(order_uuid, DECISION_VIDEO))],
        ]
    )


def build_resolved_keyboard(picked_decision: str) -> InlineKeyboardMarkup:
    """All three buttons remain visible (so the chat preserves what was
    offered), the picked one gets a ✅ prefix, and every callback is
    rewritten to ``mod_noop`` so further taps just clear the spinner.
    """
    def _row(decision: str) -> list[InlineKeyboardButton]:
        prefix = "✅ " if decision == picked_decision else ""
        return [InlineKeyboardButton(text=f"{prefix}{LABELS[decision]}", callback_data=NOOP_CALLBACK)]

    return InlineKeyboardMarkup(
        inline_keyboard=[
            _row(DECISION_ACCEPT),
            _row(DECISION_PDF),
            _row(DECISION_VIDEO),
        ]
    )
