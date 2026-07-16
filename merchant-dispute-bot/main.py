"""merchant-dispute-bot — polling Telegram bot.

Added to a Telegram group/channel that an admin has bound to a merchant
(``merchants.dispute_telegram_group_id``). The merchant or their staff posts a
message with an order id (uuid / external_id) + a receipt photo or document; the
bot forwards it to the backend, which attaches the file to the order and runs the
normal receipt premoderation.

Polling only — the bot calls the backend, the backend never calls the bot.

Operational requirements:
  * Group: disable BotFather privacy mode (/setprivacy → Disable) so the bot
    receives all messages (not just commands), otherwise it can't see receipts.
  * Channel: add the bot as an admin so it receives channel_post updates.
"""
import asyncio
import logging

import sentry_sdk
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.handlers import router
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

    dp = Dispatcher(storage=MemoryStorage())
    dp.include_router(router)

    logger.info("merchant-dispute-bot polling started")
    try:
        # Need channel_post updates too — aiogram by default subscribes to the
        # update types it has handlers for, so this is covered by the router.
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
