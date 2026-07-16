import asyncio
import logging

from app.infrastructure.db.session import WorkerSessionLocal as SessionLocal
from app.modules.stats.service import StatsService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro_func):
    """Helper to run an async function from a sync Celery task."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import threading
        thread = threading.Thread(target=lambda: asyncio.run(coro_func()))
        thread.start()
        thread.join()
    else:
        asyncio.run(coro_func())


async def _refresh_snapshot_async():
    async with SessionLocal() as session:
        try:
            service = StatsService(session)
            await service.refresh_snapshot()
            await session.commit()
            logger.info("Stats snapshot refreshed successfully")
        except Exception:
            await session.rollback()
            raise


async def _refresh_merchant_snapshots_async():
    async with SessionLocal() as session:
        try:
            service = StatsService(session)
            await service.refresh_merchant_snapshots()
            await session.commit()
            logger.info("Merchant stats snapshots refreshed successfully")
        except Exception:
            await session.rollback()
            raise


async def _snapshot_requisite_activity_async():
    async with SessionLocal() as session:
        try:
            service = StatsService(session)
            written = await service.snapshot_requisite_activity()
            # Read-only in Postgres; the CH insert is fail-safe. Commit is a
            # no-op but keeps the session-lifecycle shape consistent.
            await session.commit()
            logger.info("Requisite activity snapshot written (%d rows)", written)
        except Exception:
            await session.rollback()
            raise


@celery_app.task(name="refresh_stats_snapshot_task", bind=True, max_retries=2)
def refresh_stats_snapshot_task(self):
    logger.info("Starting stats snapshot refresh")
    try:
        _run_async(_refresh_snapshot_async)
    except Exception as e:
        logger.error(f"Failed to refresh stats snapshot: {e}")
        raise self.retry(exc=e, countdown=30)


@celery_app.task(name="refresh_merchant_stats_snapshot_task", bind=True, max_retries=2)
def refresh_merchant_stats_snapshot_task(self):
    logger.info("Starting merchant stats snapshots refresh")
    try:
        _run_async(_refresh_merchant_snapshots_async)
    except Exception as e:
        logger.error(f"Failed to refresh merchant stats snapshots: {e}")
        raise self.retry(exc=e, countdown=30)


@celery_app.task(name="snapshot_requisite_activity_task", bind=True, max_retries=1)
def snapshot_requisite_activity_task(self):
    try:
        _run_async(_snapshot_requisite_activity_async)
    except Exception as e:
        logger.error(f"Failed to snapshot requisite activity: {e}")
        raise self.retry(exc=e, countdown=30)
