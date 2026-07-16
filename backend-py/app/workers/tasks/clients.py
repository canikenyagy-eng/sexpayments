import asyncio
import logging

from app.infrastructure.db.session import WorkerSessionLocal as SessionLocal
from app.modules.clients.service import ClientService
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


async def _refresh_clients_async():
    """Off-hot-path upkeep: materialise unique clients from new orders, fold the
    Redis blocked-attempt counters into the durable column, and reconcile the
    Redis blocked-set from the DB (self-heals after a flush)."""
    # Celery runs each task in a FRESH event loop (asyncio.run below), but
    # redis_client is a module singleton whose pooled connection is bound to the
    # PREVIOUS (now-closed) loop — so the FIRST Redis call each run raises
    # "Event loop is closed". redis-py drops that connection and reconnects on
    # the next call, so we spend the hit on a throwaway ping here; otherwise it
    # landed on refresh_materialization's throttle check, which fail-closed
    # skipped the recompute EVERY beat (froze the Clients page).
    from app.infrastructure.cache.redis import redis_client
    try:
        await redis_client.ping()
    except Exception as exc:  # noqa: BLE001 — stale conn now evicted; real calls reconnect
        logger.warning("redis warm-up ping failed (stale loop connection evicted): %s", exc)

    async with SessionLocal() as session:
        try:
            service = ClientService(session)
            did_materialize = await service.refresh_materialization()
            # Fold the blocked-attempt counters: READ + stage the upsert now, then
            # ACK (consume Redis) only AFTER the commit succeeds — so a commit
            # failure leaves the deltas in Redis for the next run (no loss).
            folded = await service.reconcile_blocked_attempts()
            await session.commit()
            # Arm the materialise throttle only AFTER the commit — a failed run
            # leaves no marker, so the next beat retries (never a silent freeze).
            if did_materialize:
                await service.mark_materialized()
            await service.ack_blocked_attempts(folded)
            await service.reconcile_blocked_set()
            logger.info("Clients materialisation + blocked-attempts + blocked-set reconcile done")
        except Exception:
            await session.rollback()
            raise


@celery_app.task(name="refresh_clients_task", bind=True, max_retries=2)
def refresh_clients_task(self):
    logger.info("Starting clients refresh")
    try:
        _run_async(_refresh_clients_async)
    except Exception as e:
        logger.error(f"Failed to refresh clients: {e}")
        raise self.retry(exc=e, countdown=30)
