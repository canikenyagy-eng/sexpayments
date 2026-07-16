"""aiohttp server exposed by the support-bot.

Single endpoint: ``POST /new_receipt_for_moderation``. The backend's
``send_receipt_to_support_bot`` celery task posts a JSON payload here;
we render the admin card, attach the receipt as a document, and reply
with the ``message_id`` of the message that carries the inline keyboard
so the backend can back-fill it into the ``receipt_moderations`` row.

Auth: shared secret in ``X-Bot-Secret``. The middleware rejects any other
header value, including a missing one. The destination chat must equal
the bot's ``ALLOWED_CHAT_ID`` — mismatch means PlatformSetting and the
deployed bot are out of sync.
"""
import io
import logging
import os
from html import escape
from typing import Any

from aiogram.types import BufferedInputFile
from aiohttp import web

from bot.keyboards.moderation import build_moderation_keyboard

# Optional rendering deps for receipt previews. Guarded so the bot still runs
# (document-only, as before) if the image isn't rebuilt with these installed.
try:
    import fitz  # PyMuPDF — renders the first PDF page to a pixmap
except Exception:  # pragma: no cover - optional dependency
    fitz = None
try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover - optional dependency
    Image = None
    ImageOps = None

logger = logging.getLogger(__name__)

PREVIEW_MAX_DIM = 1600
PREVIEW_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
PREVIEW_CAPTION = "📷 <b>Превью чека</b> ⬇️"


PAYMENT_METHOD_LABELS: dict[str, str] = {
    "CARD_RUB": "Карта (RUB)",
    "SBP": "СБП",
    "CRYPTO": "Крипто",
}


def _auth_middleware(bot_secret: str):
    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.headers.get("X-Bot-Secret") != bot_secret:
            return web.json_response({"error": "Unauthorized"}, status=401)
        return await handler(request)

    return middleware


def create_app(bot, bot_secret: str, allowed_chat_id: int) -> web.Application:
    app = web.Application(middlewares=[_auth_middleware(bot_secret)])
    app["bot"] = bot
    app["allowed_chat_id"] = allowed_chat_id
    app.router.add_post("/new_receipt_for_moderation", handle_new_receipt)
    app.router.add_post("/notify_withdrawal", handle_notify_withdrawal)
    app.router.add_post("/notify_withdrawal_decided", handle_notify_withdrawal_decided)
    app.router.add_post("/remind_premoderation", handle_remind_premoderation)
    return app


async def handle_remind_premoderation(request: web.Request) -> web.Response:
    """Reply to a still-unreacted moderation card with an urgent reminder.

    The backend's reminder task posts ``chat_id`` + ``message_id`` (of the
    original check card) plus the order identifiers; we send a ``reply`` to that
    message so the nudge stays threaded under the ignored check. Auth is the
    shared ``X-Bot-Secret`` (middleware).
    """
    bot = request.app["bot"]
    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    if not chat_id or not message_id:
        return web.json_response(
            {"error": "chat_id and message_id required"}, status=400
        )

    external_id = data.get("external_id")
    order_uuid = data.get("order_uuid")
    lines = ["🔔 <b>Задержка премодерации чека!</b>"]
    if external_id:
        lines.append(f"Заявка : <code>{escape(str(external_id))}</code>")
    if order_uuid:
        lines.append(f"UUID : <code>{escape(str(order_uuid))}</code>")
    lines.append("Срочно проверьте чек!")
    text = "\n".join(lines)

    try:
        msg = await bot.send_message(
            int(chat_id), text, reply_to_message_id=int(message_id)
        )
    except Exception as exc:
        logger.exception(
            "Failed to send premoderation reminder to chat %s: %s", chat_id, exc
        )
        return web.json_response({"error": str(exc)}, status=500)

    return web.json_response({"ok": True, "message_id": msg.message_id})


_DECISION_FOOTER: dict[str, str] = {
    "approved": "✅ <b>Принято</b>",
    "rejected": "❌ <b>Отклонено</b>",
}


def _render_withdrawal_lines(data: dict[str, Any]) -> list[str]:
    """Тело карточки запроса на вывод (БЕЗ строки решения).

    Общий рендер для исходного уведомления (``/notify_withdrawal``) и для
    апдейта решения (``/notify_withdrawal_decided``) — чтобы edit переписывал
    ту же карточку 1-в-1 и лишь добавлял футер статуса снизу.
    """
    amount = data.get("amount", 0) or 0
    currency = data.get("currency", "")
    fee = data.get("fee_amount", 0) or 0
    destination = data.get("destination_address", "")
    withdrawal_id = data.get("withdrawal_id")
    user_id = data.get("user_id")
    user_login = data.get("user_login")

    lines = [
        "💸 <b>Новый запрос на вывод</b>",
        "",
        f"ID: <code>{escape(str(withdrawal_id))}</code>",
        f"Пользователь: <b>{escape(str(user_login or '—'))}</b>",
        f"ID пользователя: <code>{escape(str(user_id if user_id is not None else '—'))}</code>",
        f"Сумма: <b>{float(amount):,.2f} {escape(str(currency))}</b>",
    ]
    if fee:
        lines.append(f"Комиссия: {float(fee):,.2f} {escape(str(currency))}")
    if destination:
        lines.append(f"Адрес: <code>{escape(str(destination))}</code>")
    return lines


async def handle_notify_withdrawal(request: web.Request) -> web.Response:
    """Send a plain platform notification about a new withdrawal request.

    Unlike receipts, this is NOT pinned to ``ALLOWED_CHAT_ID`` — the platform
    notifications group is a separate, admin-configured chat (the bot just
    needs to be a member). Auth is the shared ``X-Bot-Secret`` (middleware).
    """
    bot = request.app["bot"]
    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    chat_id = data.get("chat_id")
    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)

    text = "\n".join(_render_withdrawal_lines(data))

    try:
        msg = await bot.send_message(int(chat_id), text)
    except Exception as exc:
        logger.exception("Failed to send withdrawal notification to chat %s: %s", chat_id, exc)
        return web.json_response({"error": str(exc)}, status=500)

    return web.json_response({"ok": True, "message_id": msg.message_id})


async def handle_notify_withdrawal_decided(request: web.Request) -> web.Response:
    """Дописать в карточку вывода исход модерации (approve/reject).

    Бэкенд присылает ТЕ ЖЕ поля, что и при создании, плюс ``message_id``
    исходной карточки и ``status`` (``approved`` / ``rejected``). Перерисовываем
    карточку 1-в-1 + футер статуса и редактируем исходное сообщение на месте.
    Если отредактировать нельзя (сообщение удалено / слишком старое / не
    найдено) — отправляем перерисованную карточку новым сообщением, чтобы
    решение всё равно было видно. Идемпотентно: повторный edit с тем же текстом
    («not modified») трактуется как успех (на случай ретрая Celery).
    """
    bot = request.app["bot"]
    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    chat_id = data.get("chat_id")
    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)
    message_id = data.get("message_id")

    lines = _render_withdrawal_lines(data)
    footer = _DECISION_FOOTER.get(str(data.get("status")))
    if footer:
        lines.extend(["", footer])
    text = "\n".join(lines)

    if message_id is not None:
        try:
            await bot.edit_message_text(
                text=text, chat_id=int(chat_id), message_id=int(message_id)
            )
            return web.json_response({"ok": True, "edited": True})
        except Exception as exc:
            if "not modified" in str(exc).lower():
                # Ретрай той же правки — карточка уже в финальном виде.
                return web.json_response({"ok": True, "edited": True})
            logger.warning(
                "edit_message_text failed (chat=%s msg=%s): %s — fallback to new message",
                chat_id, message_id, exc,
            )

    try:
        msg = await bot.send_message(int(chat_id), text)
    except Exception as exc:
        logger.exception("Failed to send withdrawal decision to chat %s: %s", chat_id, exc)
        return web.json_response({"error": str(exc)}, status=500)
    return web.json_response({"ok": True, "edited": False, "message_id": msg.message_id})


def _format_requisite_block(requisite: dict[str, Any] | None) -> str:
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


def _format_merchant_block(merchant: dict[str, Any] | None) -> str:
    if not isinstance(merchant, dict):
        return ""
    name = merchant.get("name")
    mid = merchant.get("id")
    if not name and not mid:
        return ""
    parts = []
    if name:
        parts.append(f"Мерчант: <b>{escape(str(name))}</b>")
    if mid is not None:
        parts.append(f"ID мерчанта: <code>{escape(str(mid))}</code>")
    return "\n".join(parts)


def _build_input_file(path: str, default_basename: str) -> tuple[BufferedInputFile, str]:
    with open(path, "rb") as f:
        content = f.read()
    ext = os.path.splitext(path)[1].lower() or ".bin"
    return BufferedInputFile(content, filename=f"{default_basename}{ext}"), ext


def _to_jpeg_preview(img: "Image.Image") -> bytes:
    """Normalise a PIL image into a Telegram-friendly JPEG: respect EXIF
    orientation, downscale to ``PREVIEW_MAX_DIM`` on the long side, flatten to
    RGB."""
    img = ImageOps.exif_transpose(img)
    if max(img.size) > PREVIEW_MAX_DIM:
        img.thumbnail((PREVIEW_MAX_DIM, PREVIEW_MAX_DIM))
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _build_preview(path: str) -> BufferedInputFile | None:
    """Render a photo preview of a receipt file so a moderator can read it at a
    glance without opening the attachment: the FIRST page for PDFs, the image
    itself (re-encoded) for images. Returns ``None`` for unsupported types or on
    ANY failure (corrupt file, missing libs) — the caller then just sends the
    document as before. Best-effort, never raises.
    """
    if Image is None:
        return None
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in PREVIEW_IMAGE_EXTS:
            with Image.open(path) as img:
                jpeg = _to_jpeg_preview(img)
        elif ext == ".pdf":
            if fitz is None:
                return None
            doc = fitz.open(path)
            try:
                if doc.page_count == 0:
                    return None
                # zoom 2× ≈ 144 DPI — crisp enough to read a receipt; the long
                # side is capped again in _to_jpeg_preview for oversized pages.
                pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(2, 2))
                png = pix.tobytes("png")
            finally:
                doc.close()
            with Image.open(io.BytesIO(png)) as img:
                jpeg = _to_jpeg_preview(img)
        else:
            return None
    except Exception as exc:
        logger.warning("Receipt preview generation failed for %s: %s", path, exc)
        return None
    return BufferedInputFile(jpeg, filename="receipt_preview.jpg")


async def handle_new_receipt(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    allowed_chat_id: int = request.app["allowed_chat_id"]

    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    chat_id: int | None = data.get("chat_id")
    if not chat_id:
        return web.json_response({"error": "chat_id required"}, status=400)
    if chat_id != allowed_chat_id:
        logger.warning(
            "Rejecting receipt: chat_id %s does not match ALLOWED_CHAT_ID %s",
            chat_id, allowed_chat_id,
        )
        return web.json_response({"error": "chat_id not allowed"}, status=403)

    order_uuid: str = data.get("order_uuid", "—")
    external_id: str = data.get("external_id", "")
    amount: float = data.get("amount", 0)
    currency: str = data.get("currency", "")
    payment_method: str = data.get("payment_method", "")
    file_paths: list[str] = data.get("file_paths") or []
    requisite = data.get("requisite")
    merchant = data.get("merchant")
    is_dispute_evidence: bool = bool(data.get("is_dispute_evidence"))

    method_label = PAYMENT_METHOD_LABELS.get(payment_method, payment_method)
    header = (
        "📥 <b>Доп. доказательство по диспуту (на модерации)</b>"
        if is_dispute_evidence
        else "📥 <b>Чек на модерации</b>"
    )
    caption_parts = [header, ""]
    merchant_block = _format_merchant_block(merchant)
    if merchant_block:
        caption_parts.append(merchant_block)
    caption_parts.append(f"Ордер: <code>{escape(str(order_uuid))}</code>")
    if external_id:
        caption_parts.append(f"Внешний ID: <code>{escape(str(external_id))}</code>")
    caption_parts.append(f"Сумма: <b>{amount:,.2f} {escape(str(currency))}</b>")
    caption_parts.append(f"Метод: {escape(method_label)}")
    requisite_block = _format_requisite_block(requisite)
    if requisite_block:
        caption_parts.append(requisite_block)
    caption = "\n".join(caption_parts)

    keyboard = build_moderation_keyboard(str(order_uuid))

    # Phase 1 — preview photo(s) FIRST, each its own message with a short
    # "file below ⬇️" caption and NO keyboard, so the moderator can read the
    # receipt at a glance. Best-effort: a render/send failure is logged and
    # skipped — the document phase below is untouched.
    for path in file_paths:
        if not path or not os.path.isfile(path):
            continue
        preview = _build_preview(path)
        if preview is None:
            continue
        try:
            await bot.send_photo(chat_id, preview, caption=PREVIEW_CAPTION)
        except Exception as exc:
            logger.exception("Failed to send preview to chat %s: %s", chat_id, exc)

    # Phase 2 — the document(s), UNCHANGED from the pre-preview behaviour: full
    # moderation card on the first attachment, inline keyboard on the last (so
    # the controls sit under the file and the returned message_id is the
    # keyboard's, which the backend stores to edit it later).
    last_message_id: int | None = None
    sent_any = False
    for idx, path in enumerate(file_paths):
        if not path or not os.path.isfile(path):
            logger.warning("Receipt file missing on disk: %s", path)
            continue
        try:
            input_file, _ext = _build_input_file(path, "receipt")
            is_last = idx == len(file_paths) - 1
            msg = await bot.send_document(
                chat_id,
                input_file,
                caption=caption if not sent_any else None,
                reply_markup=keyboard if is_last else None,
            )
            last_message_id = msg.message_id
            sent_any = True
        except Exception as exc:
            logger.exception("Failed to send file %s to chat %s: %s", path, chat_id, exc)

    if not sent_any:
        # No files attached — send the caption with the keyboard so the
        # admin can still record a decision (e.g. PDF/Video request).
        try:
            msg = await bot.send_message(chat_id, caption, reply_markup=keyboard)
            last_message_id = msg.message_id
        except Exception as exc:
            logger.exception("Failed to send fallback text to chat %s: %s", chat_id, exc)
            return web.json_response({"error": str(exc)}, status=500)

    return web.json_response({
        "ok": True,
        "attached": sent_any,
        "message_id": last_message_id,
    })
