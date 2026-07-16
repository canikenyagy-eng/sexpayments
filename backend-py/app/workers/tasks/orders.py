import asyncio
import logging

from sqlalchemy import select
from app.workers.celery_app import celery_app
from app.infrastructure.db.session import WorkerSessionLocal as SessionLocal
from app.modules.orders.service import OrderService
from app.modules.orders.models import Order
from app.common.enums.orders import OrderStatus
from app.common.types import utcnow

logger = logging.getLogger(__name__)


async def _assign_requisite_async(order_id: int):
    """
    Async wrapper for assigning a requisite.
    """
    async with SessionLocal() as session:
        try:
            order_service = OrderService(session)
            await order_service.assign_requisite(order_id)
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@celery_app.task(name="assign_requisite_task", bind=True, max_retries=3)
def assign_requisite_task(self, order_id: int):
    """
    Celery task to find and assign a requisite to an order.
    """
    logger.info(f"Starting requisite assignment for order {order_id}")
    try:
        # Run the async function in the synchronous Celery worker
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # If we are somehow in an already running loop, create a task
            loop.create_task(_assign_requisite_async(order_id))
        else:
            asyncio.run(_assign_requisite_async(order_id))
            
    except Exception as e:
        logger.error(f"Failed to assign requisite for order {order_id}: {e}")
        # Retry with exponential backoff
        raise self.retry(exc=e, countdown=2 ** self.request.retries)


async def _expire_orders_async():
    """
    Async wrapper for expiring orders that have passed their date_end.
    """
    async with SessionLocal() as session:
        now = utcnow()
        
        # Find orders that are past their date_end and still in CREATED or PENDING status
        stmt = select(Order).where(
            Order.date_end < now,
            Order.status.in_([OrderStatus.CREATED, OrderStatus.PENDING])
        )
        
        result = await session.execute(stmt)
        expired_orders = result.scalars().all()
        
        if not expired_orders:
            return
            
        logger.info(f"Found {len(expired_orders)} expired orders to fail")
        
        order_service = OrderService(session)
        
        for order in expired_orders:
            try:
                await order_service.fail_order(
                    order_id=order.id,
                    reason="Time to pay expired"
                )
            except Exception as e:
                logger.error(f"Failed to expire order {order.id}: {e}")

        await session.commit()


@celery_app.task(name="expire_orders_task", bind=True)
def expire_orders_task(self):
    """
    Celery task to periodically fail orders that have expired (passed date_end).
    """
    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import threading
            thread = threading.Thread(target=lambda: asyncio.run(_expire_orders_async()))
            thread.start()
            thread.join()
        else:
            asyncio.run(_expire_orders_async())
            
    except Exception as e:
        logger.error(f"Failed to expire orders: {e}")
        raise self.retry(exc=e, countdown=60)
