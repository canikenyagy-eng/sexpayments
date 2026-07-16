"""``/limit`` — show current min/max payin amount per method.

Asks the backend (``GET /api/bot/v1/notify/limits?chat_id=…``) which resolves
the merchant by the chat binding, then walks live local requisites through the
exact pooling gates (status, ACL, trader balance, daily / monthly remaining)
to compute the achievable amount range per method. The bot just relays the
result — all business logic is on the backend.

Reply intentionally minimal: only min/max per method (SBP, CARD), as requested.
"""
import logging
from typing import Any

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = Router()


def _fmt_amount(v: float | None) -> str:
    if v is None:
        return "—"
    # Plain integer when whole, else 2 decimals — fiat-friendly, no fractional kopeks.
    return f"{int(v)}" if float(v).is_integer() else f"{v:.2f}"


def _fmt_method(label: str, payload: dict[str, Any] | None) -> str:
    """Multi-line per-method block.

    Each surviving disjoint range is one line, so gaps between them stay visible
    to the merchant (a single overall min–max would HIDE the gaps and mislead
    them into thinking any amount in the span is accepted).
    """
    ranges = (payload or {}).get("ranges") or []
    if not ranges:
        return f"<b>{label}:</b> нет доступных реквизитов"
    lines = [f"{_fmt_amount(r.get('min'))} – {_fmt_amount(r.get('max'))}" for r in ranges]
    return "<b>" + label + ":</b>\n" + "\n".join(lines)


@router.message(Command("limit"))
async def cmd_limit(message: Message) -> None:
    chat_id = message.chat.id

    try:
        async with httpx.AsyncClient(timeout=settings.REQUEST_TIMEOUT_SECONDS) as client:
            resp = await client.get(
                f"{settings.API_BASE_URL.rstrip('/')}/api/bot/v1/notify/limits",
                headers={"X-Bot-Secret": settings.BOT_SECRET},
                params={"chat_id": str(chat_id)},
            )
    except Exception as exc:  # pragma: no cover — transient network/DNS
        logger.warning("limits fetch failed: %s", exc)
        await message.reply("Бэкенд недоступен, попробуйте позже.")
        return

    if resp.status_code == 404:
        await message.reply(
            "Этот чат не привязан к мерчанту. Пришлите chat_id "
            "(<code>/id</code>) администратору площадки."
        )
        return
    if resp.status_code in (401, 403):
        logger.error("limits auth rejected — check MERCHANT_NOTIFY_BOT_SECRET parity")
        await message.reply("Ошибка авторизации бота.")
        return
    if resp.status_code != 200:
        logger.error("limits backend error %s: %s", resp.status_code, resp.text[:200])
        await message.reply("Не удалось получить лимиты, попробуйте позже.")
        return

    try:
        data = resp.json() or {}
    except Exception:
        await message.reply("Некорректный ответ бэка.")
        return

    sbp_block = _fmt_method("СБП", data.get("sbp"))
    card_block = _fmt_method("С2С", data.get("card"))
    await message.reply(f"{sbp_block}\n\n{card_block}")
