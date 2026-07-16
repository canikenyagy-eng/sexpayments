"""Inline-keyboard helper for the "confirm order paid" button.

Callback data layout: ``confirm:<order_uuid>``. ``confirm:`` (8) + a 36-char
UUID = 44 bytes, well under Telegram's 64-byte callback limit. After a
successful confirm the button is removed entirely (``reply_markup=None``) and a
"✅ Подтверждено" footer is appended to the message text.
"""
from typing import Any

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

CALLBACK_PREFIX = "confirm"

CONFIRM_LABEL = "✅ Подтвердить заявку"


def parse_callback(data: str) -> str | None:
    """Return the ``order_uuid`` from ``confirm:<uuid>`` or ``None`` if the
    prefix/format doesn't match (handlers answer politely on ``None``)."""
    parts = data.split(":", 1)
    if len(parts) != 2 or parts[0] != CALLBACK_PREFIX or not parts[1]:
        return None
    return parts[1]


# ── Request-proof buttons (ask the merchant for video / PDF) ──────────────
# Callback layout: ``reqproof:<kind>:<order_uuid>`` — "reqproof:" (9) + "video:"
# (6) + 36-char UUID = 51 bytes, under Telegram's 64-byte callback limit.
REQUEST_PREFIX = "reqproof"
REQUEST_VIDEO_LABEL = "🎥 Запросить видео"
REQUEST_PDF_LABEL = "📄 Запросить ПДФ"


def parse_request_callback(data: str) -> tuple[str, str] | None:
    """Return ``(kind, order_uuid)`` from ``reqproof:<kind>:<uuid>`` or ``None``."""
    parts = data.split(":", 2)
    if (
        len(parts) != 3
        or parts[0] != REQUEST_PREFIX
        or parts[1] not in ("video", "pdf")
        or not parts[2]
    ):
        return None
    return parts[1], parts[2]


# ── Receipt anti-fraud check («Проверить чек» → pick provider → verdict) ──────
# ``ctx`` marks which message the keyboard belongs to so «Назад» / the verdict
# slot can restore the right original layout: "c" = «Новый чек» / dispute
# evidence (confirm + proof buttons), "d" = «Новый диспут» (check button only).
# Callback layouts: ``rcheck:<ctx>:<uuid>``, ``rcprov:<provider_id>:<ctx>:<uuid>``,
# ``rcback:<ctx>:<uuid>``. Worst case ``rcprov:<id>:c:<36-uuid>`` ≈ 49 — under the
# 64-byte cap. ``rcnoop`` is the frozen (terminal) verdict button (no handler).
CHECK_PREFIX = "rcheck"
PROVIDER_PREFIX = "rcprov"
BACK_PREFIX = "rcback"
NOOP_CALLBACK = "rcnoop"

CHECK_LABEL = "🔎 Проверить чек"
BACK_LABEL = "◀️ Назад"
WAIT_LABEL = "⏳ Ожидание"
RESULT_CLEAN_LABEL = "🟢 Настоящий"
RESULT_FAKE_LABEL = "🔴 Фейк"
RESULT_ERROR_LABEL = "⚠️ Ошибка"

CHECK_CONTEXTS = ("c", "d")


def build_check_keyboard(
    order_uuid: str,
    *,
    ctx: str = "c",
    check_label: str = CHECK_LABEL,
    check_callback: str | None = None,
) -> InlineKeyboardMarkup:
    """Keyboard under a receipt message.

    ``ctx="c"`` («Новый чек» / dispute evidence): confirm-paid, «Проверить чек»,
    then request video/PDF. ``ctx="d"`` («Новый диспут»): only «Проверить чек».
    The check-button slot is parametrised (``check_label`` / ``check_callback``)
    so the same builder renders «Ожидание» or a frozen verdict in its place while
    keeping the rest of the original buttons intact."""
    cb = check_callback or f"{CHECK_PREFIX}:{ctx}:{order_uuid}"
    check_row = [InlineKeyboardButton(text=check_label, callback_data=cb)]
    if ctx == "d":
        return InlineKeyboardMarkup(inline_keyboard=[check_row])
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=CONFIRM_LABEL, callback_data=f"{CALLBACK_PREFIX}:{order_uuid}")],
        check_row,
        [
            InlineKeyboardButton(text=REQUEST_VIDEO_LABEL, callback_data=f"{REQUEST_PREFIX}:video:{order_uuid}"),
            InlineKeyboardButton(text=REQUEST_PDF_LABEL, callback_data=f"{REQUEST_PREFIX}:pdf:{order_uuid}"),
        ],
    ])


def build_provider_keyboard(order_uuid: str, ctx: str, providers: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    """Swap a check message's buttons to a provider picker (name · price) + «Назад»."""
    rows = []
    for p in providers:
        price = p.get("price_usdt") or 0
        rows.append([InlineKeyboardButton(
            text=f"{p.get('name')} · {price:g} USDT",
            callback_data=f"{PROVIDER_PREFIX}:{p.get('id')}:{ctx}:{order_uuid}",
        )])
    rows.append([InlineKeyboardButton(text=BACK_LABEL, callback_data=f"{BACK_PREFIX}:{ctx}:{order_uuid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parse_check_callback(data: str) -> tuple[str, str] | None:
    """``(ctx, order_uuid)`` from ``rcheck:<ctx>:<uuid>`` or ``None``."""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != CHECK_PREFIX or parts[1] not in CHECK_CONTEXTS or not parts[2]:
        return None
    return parts[1], parts[2]


def parse_back_callback(data: str) -> tuple[str, str] | None:
    """``(ctx, order_uuid)`` from ``rcback:<ctx>:<uuid>`` or ``None``."""
    parts = data.split(":", 2)
    if len(parts) != 3 or parts[0] != BACK_PREFIX or parts[1] not in CHECK_CONTEXTS or not parts[2]:
        return None
    return parts[1], parts[2]


def parse_provider_callback(data: str) -> tuple[int, str, str] | None:
    """``(provider_id, ctx, order_uuid)`` from ``rcprov:<id>:<ctx>:<uuid>`` or ``None``."""
    parts = data.split(":", 3)
    if len(parts) != 4 or parts[0] != PROVIDER_PREFIX or parts[2] not in CHECK_CONTEXTS or not parts[3]:
        return None
    try:
        pid = int(parts[1])
    except ValueError:
        return None
    return pid, parts[2], parts[3]
