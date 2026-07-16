"""Forward inbound receipt messages to the backend dispute-intake endpoint.

A valid message carries an order identifier (uuid or external_id) somewhere in
its text/caption AND a photo or document. We forward the FULL message text — the
backend maps chat_id → merchant (the chat binding is the trust boundary), applies
the merchant's id-mask to pull OUR order id out of the text (our id may not be
the first token — the merchant's own id often is), then attaches the file and
runs premoderation. We also send a best-guess single token for backwards-compat,
but the mask over the full text is the primary path.
"""
import io
import logging
import re

import httpx
from aiogram import Bot, F, Router
from aiogram.types import Message

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = Router()

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _extract_identifier(text: str | None) -> str | None:
    """UUID anywhere in the text wins; otherwise the first whitespace token is
    treated as the merchant external_id (messages are usually just "id + file")."""
    if not text:
        return None
    m = _UUID_RE.search(text)
    if m:
        return m.group(0)
    tokens = text.split()
    return tokens[0].strip() if tokens else None


async def _download(bot: Bot, file_id: str) -> bytes:
    tg_file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await bot.download_file(tg_file.file_path, buf)
    return buf.getvalue()


async def _reply(message: Message, text: str) -> None:
    # Channels / restricted chats may forbid replies — best-effort, never raise.
    try:
        await message.reply(text)
    except Exception:
        try:
            await message.answer(text)
        except Exception:
            pass


async def _handle(message: Message, bot: Bot) -> None:
    filename = "receipt"
    file_id: str | None = None
    if message.photo:
        file_id = message.photo[-1].file_id  # largest size
        filename = "receipt.jpg"
    elif message.document:
        file_id = message.document.file_id
        filename = message.document.file_name or "receipt.bin"
    if not file_id:
        return  # not a receipt-bearing message

    body_text = (message.caption or message.text or "").strip()

    if not body_text:
        return
    # Best-guess single token (legacy fallback). The backend applies the
    # merchant's mask to the FULL text, so we always send the whole message.
    identifier = _extract_identifier(body_text)

    try:
        content = await _download(bot, file_id)
    except Exception as exc:  # pragma: no cover — telegram download flake
        logger.warning("download failed: %s", exc)
        return  # silent — don't react in chat to a transient flake

    try:
        async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.post(
                f"{settings.API_BASE_URL.rstrip('/')}/api/bot/v1/dispute/receipt",
                headers={"X-Bot-Secret": settings.BOT_SECRET},
                data={
                    "chat_id": str(message.chat.id),
                    "text": body_text,
                    **({"identifier": identifier} if identifier else {}),
                },
                files={"attachment": (filename, content)},
            )
    except Exception as exc:
        logger.warning("backend post failed: %s", exc)
        return  # silent

    if resp.status_code == 200:
        order_uuid = ""
        try:
            order_uuid = (resp.json() or {}).get("order_uuid") or ""
        except Exception:
            pass
        suffix = f" <code>{order_uuid}</code>" if order_uuid else ""
        await _reply(message, f"✅ Чек принят по заявке{suffix}.")
    elif resp.status_code in (401, 403):
        logger.error("backend auth rejected — check MERCHANT_DISPUTE_BOT_SECRET")
    elif resp.status_code >= 500:
        logger.error("backend error %s: %s", resp.status_code, resp.text[:200])


@router.message(F.photo | F.document)
async def on_message(message: Message, bot: Bot) -> None:
    await _handle(message, bot)


@router.channel_post(F.photo | F.document)
async def on_channel_post(message: Message, bot: Bot) -> None:
    await _handle(message, bot)
