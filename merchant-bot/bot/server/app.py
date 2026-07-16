"""Inbound HTTP server for merchant-bot.

The backend pushes a PDF/video proof request here for BOT-created orders
(``POST /proof_requested``, symmetric ``X-Bot-Secret``). The bot posts the
request into the order's chat with an «attach proof» button that reuses the
existing upload-receipt flow (``payment:upload_receipt:{merchant_id}:{order_uuid}``)
— so the merchant uploads the video/PDF the same way they confirm an order.
"""
import logging
from typing import Any

from aiohttp import web

from bot.keyboards.inline import proof_request_keyboard

logger = logging.getLogger(__name__)


def _auth_middleware(bot_secret: str):
    @web.middleware
    async def middleware(request: web.Request, handler):
        if request.headers.get("X-Bot-Secret") != bot_secret:
            return web.json_response({"error": "Unauthorized"}, status=401)
        return await handler(request)
    return middleware


async def handle_proof_requested(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    try:
        data: dict[str, Any] = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)

    chat_id: int | None = data.get("chat_id")
    text: str = data.get("text") or "По ордеру запрошен дополнительный пруф."
    order_uuid: str | None = data.get("order_uuid")
    merchant_id: int | None = data.get("merchant_id")
    if chat_id is None or not order_uuid or merchant_id is None:
        return web.json_response(
            {"error": "chat_id, order_uuid, merchant_id required"}, status=400
        )

    caption = (
        f"{text}\n\n"
        "Нажмите кнопку и пришлите файл — так же, как при подтверждении заявки."
    )
    try:
        await bot.send_message(
            int(chat_id),
            caption,
            reply_markup=proof_request_keyboard(str(order_uuid), int(merchant_id)),
        )
    except Exception as exc:
        logger.warning("Failed to push proof request to chat %s: %s", chat_id, exc)
        return web.json_response({"error": str(exc)}, status=500)
    return web.json_response({"ok": True})


def create_app(bot, bot_secret: str) -> web.Application:
    app = web.Application(middlewares=[_auth_middleware(bot_secret)])
    app["bot"] = bot
    app.router.add_post("/proof_requested", handle_proof_requested)
    return app
