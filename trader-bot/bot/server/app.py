import logging
import os
from html import escape
from typing import Any

from aiogram.types import BufferedInputFile
from aiohttp import web

from bot.keyboards.confirm import build_check_keyboard

logger = logging.getLogger(__name__)

PAYMENT_METHOD_LABELS: dict[str, str] = {
    "CARD_RUB": "Карта (RUB)",
    "SBP": "СБП",
    "CRYPTO": "Крипто",
}

DISPUTE_REASON_LABELS: dict[str, str] = {
    "NOT_PAID": "Не оплачено",
    "WRONG_AMOUNT": "Неверная сумма",
    "OTHER": "Другое",
}


def _format_receipt_check_line(check: dict[str, Any] | None) -> str:
    """Single-line verdict from the receipt anti-fraud check.

    `check` matches the payload built by backend `_build_receipt_check_payload`:
    ``{"status": str, "is_clean": bool|None, "error_code": str|None}``.
    Returns an empty string when no check is attached so the caller can
    safely concatenate.
    """
    if not isinstance(check, dict):
        return ""
    status = (check.get("status") or "").lower()
    if status == "failed":
        return "<b>Проверка:</b> ⚠️ ошибка проверки"
    if status == "pending":
        return "<b>Проверка:</b> ⏳ выполняется"
    is_clean = check.get("is_clean")
    if is_clean is True:
        return "<b>Проверка:</b> ✅ чек чист"
    if is_clean is False:
        return "<b>Проверка:</b> ❌ подозрение"
    return ""


def _format_requisite_block(requisite: dict[str, Any] | None) -> str:
    """Build HTML lines describing the requisite. Empty string if no data."""
    if not isinstance(requisite, dict):
        return ""

    nickname = requisite.get("nickname")
    bank_name = requisite.get("bank_name")
    account_number = requisite.get("account_number")
    account_holder = requisite.get("account_holder")

    lines: list[str] = ["", "<b>Реквизит</b>"]
    if nickname:
        lines.append(f"Название: {escape(str(nickname))}")
    if bank_name:
        lines.append(f"Банк: {escape(str(bank_name))}")
    if account_number:
        lines.append(f"Реквизит: <code>{escape(str(account_number))}</code>")
    if account_holder:
        lines.append(f"Имя: {escape(str(account_holder))}")

    if len(lines) <= 2:
        return ""
    return "\n".join(lines)

def _auth_middleware(bot_secret: str):
    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.headers.get("X-Bot-Secret") != bot_secret:
            return web.json_response({"error": "Unauthorized"}, status=401)
        return await handler(request)
    return middleware


def create_app(bot, bot_secret: str) -> web.Application:
    app = web.Application(middlewares=[_auth_middleware(bot_secret)])
    app["bot"] = bot
    app.router.add_post("/new_check_uploaded", handle_new_check_uploaded)
    app.router.add_post("/doliv_check", handle_doliv_check)
    app.router.add_post("/new_dispute", handle_new_dispute)
    app.router.add_post("/broadcast", handle_broadcast)
    return app


async def handle_broadcast(request: web.Request) -> web.Response:
    """Admin broadcast → one trader chat. Plain text (parse_mode disabled) so the
    admin's message — emoji and any characters — is delivered verbatim. Returns
    500 on a per-recipient send failure (blocked bot, chat gone) so the backend
    counts it as ``failed``."""
    bot = request.app["bot"]
    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    chat_id: int | None = data.get("chat_id")
    text: str = data.get("text", "")
    if not chat_id or not text:
        return web.json_response({"error": "chat_id and text required"}, status=400)

    try:
        await bot.send_message(chat_id, text, parse_mode=None)
    except Exception as exc:
        logger.warning("Failed to broadcast to chat %s: %s", chat_id, exc)
        return web.json_response({"error": str(exc)}, status=500)
    return web.json_response({"ok": True})


def _build_input_file(path: str, default_basename: str) -> tuple[BufferedInputFile, str]:
    """Read file from disk and wrap it for aiogram as a document. Returns (input_file, ext)."""
    with open(path, "rb") as f:
        content = f.read()
    ext = os.path.splitext(path)[1].lower()
    if not ext:
        ext = ".bin"
    filename = f"{default_basename}{ext}"
    return BufferedInputFile(content, filename=filename), ext


async def _send_files_with_caption(
    bot,
    chat_id: int,
    file_paths: list[str],
    caption: str,
    default_basename: str,
    reply_markup=None,
) -> bool:
    """Send each file as a document (no compression). Caption goes only on the
    first attachment; ``reply_markup`` (e.g. the confirm button) is attached to
    the LAST attachment so the controls sit right under the receipt.

    Returns True if at least one attachment was sent successfully.
    """
    valid = [p for p in file_paths if p and os.path.isfile(p)]
    for p in file_paths:
        if p and not os.path.isfile(p):
            logger.warning("Receipt file not found on disk: %s", p)
    sent = False
    for idx, path in enumerate(valid):
        is_last = idx == len(valid) - 1
        try:
            input_file, ext = _build_input_file(path, default_basename)
            await bot.send_document(
                chat_id,
                input_file,
                caption=caption if not sent else None,
                reply_markup=reply_markup if is_last else None,
            )
            logger.info(
                "Sent attachment to chat %s: path=%s ext=%s",
                chat_id,
                path,
                ext,
            )
            sent = True
        except Exception as exc:
            logger.exception("Failed to send file %s to chat %s: %s", path, chat_id, exc)
    return sent


async def handle_new_check_uploaded(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    chat_id: int | None = data.get("chat_id")
    order_uuid: str = data.get("order_uuid", "—")
    amount: float = data.get("amount", 0)
    currency: str = data.get("currency", "")
    payment_method: str = data.get("payment_method", "")
    file_paths: list[str] = data.get("file_paths") or []
    requisite: dict[str, Any] | None = data.get("requisite")
    receipt_check: dict[str, Any] | None = data.get("receipt_check")
    is_dispute_evidence: bool = bool(data.get("is_dispute_evidence"))

    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)

    method_label = PAYMENT_METHOD_LABELS.get(payment_method, payment_method)
    header = (
        "📎 <b>Доп. доказательство по диспуту</b>"
        if is_dispute_evidence
        else "📎 <b>Новый чек</b>"
    )
    caption = (
        f"{header}\n\n"
        f"Ордер: <code>{escape(str(order_uuid))}</code>\n"
        f"Сумма: <b>{amount:,.2f} {escape(str(currency))}</b>\n"
        f"Метод: {escape(method_label)}"
    )
    check_line = _format_receipt_check_line(receipt_check)
    if check_line:
        caption = f"{caption}\n{check_line}"
    requisite_block = _format_requisite_block(requisite)
    if requisite_block:
        caption = f"{caption}\n{requisite_block}"

    # Confirm the order paid, or ask the merchant for stronger proof (video /
    # PDF). Carries the order uuid so the callback handlers know which order.
    keyboard = build_check_keyboard(str(order_uuid))

    sent = await _send_files_with_caption(
        bot=bot,
        chat_id=chat_id,
        file_paths=file_paths,
        caption=caption,
        default_basename="receipt",
        reply_markup=keyboard,
    )

    if not sent:
        try:
            await bot.send_message(chat_id, caption, reply_markup=keyboard)
        except Exception as exc:
            logger.exception("Failed to send text notification to chat %s: %s", chat_id, exc)
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response({"ok": True, "attached": False})

    return web.json_response({"ok": True, "attached": True})


async def handle_doliv_check(request: web.Request) -> web.Response:
    """Deliver a доливщик's receipt to the requester. Like a new-check notice but
    for a долив — no «подтвердить» button (the requester only receives the proof)."""
    bot = request.app["bot"]
    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    chat_id: int | None = data.get("chat_id")
    doliv_uuid: str = data.get("doliv_uuid", "—")
    amount: float = data.get("amount", 0)
    currency: str = data.get("currency", "")
    payment_method: str = data.get("payment_method", "")
    file_paths: list[str] = data.get("file_paths") or []
    requisite: dict[str, Any] | None = data.get("requisite")

    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)

    method_label = PAYMENT_METHOD_LABELS.get(payment_method, payment_method)
    caption = (
        f"📎 <b>Чек по доливу</b>\n\n"
        f"Долив: <code>{escape(str(doliv_uuid))}</code>\n"
        f"Сумма: <b>{amount:,.2f} {escape(str(currency))}</b>\n"
        f"Метод: {escape(method_label)}"
    )
    requisite_block = _format_requisite_block(requisite)
    if requisite_block:
        caption = f"{caption}\n{requisite_block}"

    sent = await _send_files_with_caption(
        bot=bot,
        chat_id=chat_id,
        file_paths=file_paths,
        caption=caption,
        default_basename="receipt",
    )
    if not sent:
        try:
            await bot.send_message(chat_id, caption)
        except Exception as exc:
            logger.exception("Failed to send doliv notification to chat %s: %s", chat_id, exc)
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response({"ok": True, "attached": False})

    return web.json_response({"ok": True, "attached": True})


async def handle_new_dispute(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    chat_id: int | None = data.get("chat_id")
    dispute_uuid: str = data.get("dispute_uuid", "—")
    order_uuid: str = data.get("order_uuid", "—")
    reason: str = data.get("reason", "")
    description: str = data.get("description", "")
    amount: float = data.get("amount", 0)
    currency: str = data.get("currency", "")
    file_paths: list[str] = data.get("file_paths") or []
    requisite: dict[str, Any] | None = data.get("requisite")

    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)

    reason_label = DISPUTE_REASON_LABELS.get(reason, reason)
    text = (
        f"⚠️ <b>Новый диспут</b>\n\n"
        f"Диспут: <code>{escape(str(dispute_uuid)[:8])}</code>\n"
        f"Ордер: <code>{escape(str(order_uuid))}</code>\n"
        f"Сумма: <b>{amount:,.2f} {escape(str(currency))}</b>\n"
        f"Причина: {escape(reason_label)}\n"
        f"Описание: {escape(str(description))}"
    )
    requisite_block = _format_requisite_block(requisite)
    if requisite_block:
        text = f"{text}\n{requisite_block}"

    # Dispute context ("d") → only the «Проверить чек» button (no confirm/proof).
    keyboard = build_check_keyboard(str(order_uuid), ctx="d")

    sent = await _send_files_with_caption(
        bot=bot,
        chat_id=chat_id,
        file_paths=file_paths,
        caption=text,
        default_basename="evidence",
        reply_markup=keyboard,
    )

    if not sent:
        try:
            await bot.send_message(chat_id, text, reply_markup=keyboard)
        except Exception as exc:
            logger.exception("Failed to send dispute text notification to chat %s: %s", chat_id, exc)
            return web.json_response({"error": str(exc)}, status=500)
        return web.json_response({"ok": True, "attached": False})

    return web.json_response({"ok": True, "attached": True})
