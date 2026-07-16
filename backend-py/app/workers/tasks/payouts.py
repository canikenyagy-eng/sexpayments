"""Periodic payout maintenance (Celery beat):

  * expire_payouts_task        — TTL reached → EXPIRED + refund the terminal.
  * return_stale_payout_claims_task — claim lapsed → back to the pool.
  * release_payout_holds_task  — trader hold elapsed → ESCROW → WORK.

Each task iterates the due rows and calls the matching PayoutService method
(which owns the money + status transition); the worker just commits.
"""
import asyncio
import logging

from app.common.types import utcnow
from app.infrastructure.db.session import WorkerSessionLocal as SessionLocal
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run(coro_factory):
    """Run an async coroutine factory from a sync Celery task, tolerating an
    already-running loop (mirrors expire_orders_task)."""
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


async def _expire_payouts_async():
    from app.modules.payouts.repository import PayoutRepository
    from app.modules.payouts.service import PayoutService

    async with SessionLocal() as session:
        rows = await PayoutRepository(session).list_expired(utcnow())
        if not rows:
            return
        svc = PayoutService(session)
        for p in rows:
            try:
                await svc.expire_payout(p)
            except Exception as e:
                logger.error("expire payout %s failed: %s", p.id, e)
        await session.commit()


async def _return_stale_claims_async():
    from app.modules.payouts.repository import PayoutRepository
    from app.modules.payouts.service import PayoutService

    async with SessionLocal() as session:
        rows = await PayoutRepository(session).list_stale_claims(utcnow())
        if not rows:
            return
        svc = PayoutService(session)
        for p in rows:
            try:
                await svc.return_stale_claim(p)
            except Exception as e:
                logger.error("return stale claim %s failed: %s", p.id, e)
        await session.commit()


async def _release_holds_async():
    from app.modules.payouts.repository import PayoutRepository
    from app.modules.payouts.service import PayoutService

    async with SessionLocal() as session:
        rows = await PayoutRepository(session).list_holds_due(utcnow())
        if not rows:
            return
        svc = PayoutService(session)
        for p in rows:
            try:
                await svc.release_hold(p)
            except Exception as e:
                logger.error("release payout hold %s failed: %s", p.id, e)
        await session.commit()


@celery_app.task(name="expire_payouts_task", bind=True)
def expire_payouts_task(self):
    try:
        _run(_expire_payouts_async)
    except Exception as e:
        logger.error("expire_payouts_task failed: %s", e)
        raise self.retry(exc=e, countdown=60)


@celery_app.task(name="return_stale_payout_claims_task", bind=True)
def return_stale_payout_claims_task(self):
    try:
        _run(_return_stale_claims_async)
    except Exception as e:
        logger.error("return_stale_payout_claims_task failed: %s", e)
        raise self.retry(exc=e, countdown=60)


@celery_app.task(name="release_payout_holds_task", bind=True)
def release_payout_holds_task(self):
    try:
        _run(_release_holds_async)
    except Exception as e:
        logger.error("release_payout_holds_task failed: %s", e)
        raise self.retry(exc=e, countdown=60)
