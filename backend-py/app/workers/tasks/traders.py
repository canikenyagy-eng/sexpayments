import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select, update, func, and_
from app.workers.celery_app import celery_app
from app.infrastructure.db.session import WorkerSessionLocal as async_session_maker
from app.core.config import get_settings
from app.modules.traders.models import Trader
from app.modules.requisites.models import Requisite
from app.common.enums.traders import TraderStatus
from app.common.types import utcnow

logger = logging.getLogger(__name__)
settings = get_settings()


async def _auto_disable_traders_payin_async():
    """
    Find traders who have payin active, but all their requisites have been disabled/inactive
    for more than TRADER_AUTO_DISABLE_PAYIN_MINUTES.
    Disable their payin.
    """
    timeout_minutes = settings.TRADER_AUTO_DISABLE_PAYIN_MINUTES
    cutoff_time = utcnow() - timedelta(minutes=timeout_minutes)

    async with async_session_maker() as session:
        # WorkerSessionLocal has no auto-commit. Wrap the entire unit of
        # work in `session.begin()` FIRST — before any execute() opens an
        # implicit transaction that would clash with a later explicit
        # `begin()` (the classic `InvalidRequestError: A transaction is
        # already begun on this Session.` trap that flooded prod logs).
        async with session.begin():
            # Subquery: traders that still have at least one active or
            # recently-active requisite. We'll exclude them from the
            # disable list below.
            active_or_recent_reqs = select(Requisite.trader_id).where(
                and_(
                    Requisite.is_archived == False,
                    (Requisite.is_active == True)
                    | (Requisite.status_updated_at >= cutoff_time),
                )
            )

            stmt = select(Trader).where(
                Trader.is_payin_active == True,
                Trader.status == TraderStatus.ENABLED,
                ~Trader.user_id.in_(active_or_recent_reqs),
            )

            traders_to_disable = (await session.execute(stmt)).scalars().all()
            if not traders_to_disable:
                return

            trader_ids = [t.id for t in traders_to_disable]
            logger.info(
                "Auto-disabling payin for %d traders (no active requisites > %sm)",
                len(trader_ids), timeout_minutes,
            )

            await session.execute(
                update(Trader)
                .where(Trader.id.in_(trader_ids))
                .values(is_payin_active=False)
            )


@celery_app.task(name="auto_disable_traders_payin_task", bind=True)
def auto_disable_traders_payin_task(self):
    """
    Celery task to periodically disable payin for traders with no active requisites.
    """
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import threading
            thread = threading.Thread(target=lambda: asyncio.run(_auto_disable_traders_payin_async()))
            thread.start()
            thread.join()
        else:
            asyncio.run(_auto_disable_traders_payin_async())
            
    except Exception as e:
        logger.error(f"Failed to auto-disable traders payin: {e}")
        raise self.retry(exc=e, countdown=60)
