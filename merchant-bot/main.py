import asyncio
import contextlib
import logging

import sentry_sdk
import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from aiohttp import web

from bot.handlers import router
from bot.handlers.payments import _fmt_final_notification
from bot.server.app import create_app
from bot.services.api_client import ApiClientError, MerchantBotApiClient
from bot.utils.tracker import ActivePaymentTracker
from config import get_settings

settings = get_settings()

logger = logging.getLogger(__name__)


async def status_watcher(
    bot: Bot,
    api_client: MerchantBotApiClient,
    tracker: ActivePaymentTracker,
) -> None:
    """Poll active orders and push notifications on final status."""
    while True:
        for order_id, tracked in tracker.snapshot():
            try:
                order = await api_client.get_order(
                    tracked.tg_user_id, tracked.merchant_id, order_id
                )
            except ApiClientError:
                continue
            except Exception:
                continue

            if order.status != tracked.status:
                tracker.update_status(order_id, order.status)

            if order.is_final:
                try:
                    await bot.send_message(
                        tracked.chat_id,
                        _fmt_final_notification(order),
                    )
                except Exception as exc:
                    logger.warning("Failed to send final notification: %s", exc)
                tracker.untrack(order_id)

        await asyncio.sleep(max(3, settings.STATUS_POLL_INTERVAL_SECONDS))


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    if settings.SENTRY_DSN:
        sentry_sdk.init(
            dsn=settings.SENTRY_DSN,
            traces_sample_rate=0.2,
        )

    timeout = aiohttp.ClientTimeout(total=settings.REQUEST_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        api_client = MerchantBotApiClient(session=session, settings=settings)
        tracker = ActivePaymentTracker()

        bot_session = AiohttpSession(proxy=settings.TELEGRAM_PROXY)
        bot = Bot(
            token=settings.BOT_TOKEN,
            session=bot_session,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

        dp = Dispatcher(storage=MemoryStorage())
        dp.include_router(router)
        dp["api_client"] = api_client
        dp["tracker"] = tracker

        # Inbound HTTP server (backend → /proof_requested) alongside polling.
        web_app = create_app(bot, settings.BOT_SECRET)
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, settings.SERVER_HOST, settings.SERVER_PORT)
        await site.start()
        logger.info("HTTP server listening on %s:%d", settings.SERVER_HOST, settings.SERVER_PORT)

        watcher_task = asyncio.create_task(status_watcher(bot, api_client, tracker))
        try:
            await dp.start_polling(bot)
        finally:
            watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await watcher_task
            await runner.cleanup()
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
