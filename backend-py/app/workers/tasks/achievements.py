"""Background jobs for the trader achievements/bonuses feature.

Nothing here runs on the order hot path. Two jobs keep the materialized bonus
(``traders.achievement_bonus_percent``, read for free in
``OrderService._calculate_trader_fee``) fresh:

  * ``refresh_trader_turnover_task`` (~every 5 min) — re-aggregate TODAY's
    per-trader turnover into the rollup and recompute bonuses, so the daily-tier
    part reacts to the trader's live running turnover.
  * ``finalize_trader_day_task`` (nightly) — finalize YESTERDAY's rollup (catch
    late-settled orders) and recompute, so streaks advance at the day boundary.

Both are best-effort: a failure is logged + retried, never poisons order flow.
"""
import asyncio
import logging
from datetime import timedelta

from app.common.types import utcnow
from app.infrastructure.db.session import WorkerSessionLocal as async_session_maker
from app.modules.achievements.service import AchievementService
from app.modules.settings.service import SettingsService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run(coro_factory) -> None:
    """Run an async unit of work from a sync Celery task (same guard the other
    tasks use to avoid nesting event loops)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import threading

        thread = threading.Thread(target=lambda: asyncio.run(coro_factory()))
        thread.start()
        thread.join()
    else:
        asyncio.run(coro_factory())


async def _refresh_current_turnover_async() -> None:
    async with async_session_maker() as session:
        # WorkerSessionLocal has no auto-commit — open the transaction first.
        async with session.begin():
            svc = AchievementService(session)
            today = utcnow().date()
            # Skip the aggregate when the feature is off; recompute still runs so a
            # freshly-disabled feature clears any stale bonuses to 0.
            if await SettingsService(session).get_bool("achievements_enabled"):
                await svc.refresh_volume_for_day(today)
            changed = await svc.recompute_bonuses(today=today)
    logger.info("achievements: turnover refresh done, %d bonuses changed", changed)


async def _finalize_previous_day_async() -> None:
    async with async_session_maker() as session:
        async with session.begin():
            svc = AchievementService(session)
            today = utcnow().date()
            yesterday = today - timedelta(days=1)
            if await SettingsService(session).get_bool("achievements_enabled"):
                await svc.refresh_volume_for_day(yesterday)
            changed = await svc.recompute_bonuses(today=today)
    logger.info("achievements: finalized %s, %d bonuses changed", yesterday, changed)


@celery_app.task(name="refresh_trader_turnover_task", bind=True)
def refresh_trader_turnover_task(self):
    try:
        _run(_refresh_current_turnover_async)
    except Exception as exc:  # noqa: BLE001 — best-effort telemetry job
        logger.error("refresh_trader_turnover_task failed: %s", exc)
        raise self.retry(exc=exc, countdown=60)


@celery_app.task(name="finalize_trader_day_task", bind=True)
def finalize_trader_day_task(self):
    try:
        _run(_finalize_previous_day_async)
    except Exception as exc:  # noqa: BLE001 — best-effort telemetry job
        logger.error("finalize_trader_day_task failed: %s", exc)
        raise self.retry(exc=exc, countdown=300)
