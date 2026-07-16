"""aiohttp server for merchant-notify-bot.

Single endpoint ``POST /moderation_request`` — backend posts here when an
admin clicks "Запросить PDF/Видео" in support-bot. The bot forwards the
text into the merchant's notify group (chat_id provided by the backend
from ``merchants.notify_telegram_group_id``).

Auth: shared secret in ``X-Bot-Secret``. The bot does not verify the
chat — it trusts the backend to send only chat_ids it has stored for a
merchant.
"""
import logging
from typing import Any

from aiohttp import web

logger = logging.getLogger(__name__)


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
    app.router.add_post("/moderation_request", handle_moderation_request)
    return app


async def handle_moderation_request(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    chat_id = data.get("chat_id")
    text = data.get("text")
    if not chat_id or not text:
        return web.json_response({"error": "chat_id and text are required"}, status=400)

    try:
        msg = await bot.send_message(chat_id, text)
    except Exception as exc:
        logger.exception("Failed to send moderation_request to %s: %s", chat_id, exc)
        return web.json_response({"error": str(exc)}, status=500)

    return web.json_response({"ok": True, "message_id": msg.message_id})
