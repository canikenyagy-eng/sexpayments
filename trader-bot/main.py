import asyncio
import contextlib
import logging

import sentry_sdk
from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers import build_router
from bot.server.app import create_app
from bot.services.backend_client import BackendClient
from config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    if settings.SENTRY_DSN:
        sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.1)

    bot_session = AiohttpSession(proxy=settings.TELEGRAM_PROXY)
    bot = Bot(
        token=settings.BOT_TOKEN,
        session=bot_session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    backend = BackendClient(
        base_url=settings.BACKEND_API_URL,
        bot_secret=settings.BACKEND_BOT_SECRET,
    )

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(build_router(backend))

    web_app = create_app(bot, settings.BOT_SECRET)
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, settings.SERVER_HOST, settings.SERVER_PORT)
    await site.start()
    logger.info("HTTP server listening on %s:%d", settings.SERVER_HOST, settings.SERVER_PORT)

    try:
        await dp.start_polling(bot)
    finally:
        with contextlib.suppress(Exception):
            await backend.close()
        await runner.cleanup()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
