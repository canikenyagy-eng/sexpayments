import asyncio
import logging

from app.common.types import utcnow
from app.workers.celery_app import celery_app
from app.infrastructure.db.session import WorkerSessionLocal as async_session_maker
from app.modules.rates.service import RateService

logger = logging.getLogger(__name__)


async def _sync_all_active_rates_async():
    """
    Async wrapper for syncing all active rates based on their update_interval_seconds.
    """
    async with async_session_maker() as session:
        rate_service = RateService(session)
        configs = await rate_service.repository.get_active_configs()
        
        now = utcnow()
        errors = []
        
        for config in configs:
            try:
                # Check if it's time to update this specific config
                if config.last_updated_at:
                    time_since_update = (now - config.last_updated_at).total_seconds()
                    # Add a small buffer (e.g., 2 seconds) to avoid missing an update due to slight delays
                    if time_since_update < (config.update_interval_seconds - 2):
                        continue
                        
                logger.info(f"Syncing rate for config {config.id} ({config.name})")
                await rate_service.update_rate(config.id)
            except Exception as e:
                logger.error(f"Failed to sync rate for config {config.id}: {e}")
                errors.append(e)

        await session.commit()

        if errors:
            raise Exception(f"Failed to sync {len(errors)} rate configs")


@celery_app.task(name="sync_all_active_rates_task", bind=True, max_retries=3)
def sync_all_active_rates_task(self):
    """
    Celery task to sync all active rate configurations.
    """
    logger.info("Starting sync for all active rates")
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import threading
            thread = threading.Thread(target=lambda: asyncio.run(_sync_all_active_rates_async()))
            thread.start()
            thread.join()
        else:
            asyncio.run(_sync_all_active_rates_async())
            
    except Exception as e:
        logger.error(f"Failed to sync active rates: {e}")
        raise self.retry(exc=e, countdown=2 ** self.request.retries)
