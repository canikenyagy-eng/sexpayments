from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_service, get_db
from app.modules.users.permissions import require_admin
from app.modules.users.models import User
from app.modules.orders.service import OrderService
from app.modules.orders.schemas import CallbackAttemptResponse
from app.modules.callbacks.repository import CallbackAttemptRepository
from app.workers.tasks.callbacks import send_order_callback
from app.modules.audit.service import AuditService

router = APIRouter()


@router.get(
    "/attempts",
    response_model=List[CallbackAttemptResponse],
    summary="List callback attempts (Admin)",
    dependencies=[Depends(require_admin)],
)
async def list_callback_attempts(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    order_id: Optional[int] = Query(None, description="Filter by order ID"),
    is_successful: Optional[bool] = Query(None, description="Filter by success status"),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    session: AsyncSession = Depends(get_db),
):
    repo = CallbackAttemptRepository(session)
    return await repo.list_with_filters(
        skip=skip,
        limit=limit,
        order_id=order_id,
        is_successful=is_successful,
        sort_order=sort_order,
    )

@router.post(
    "/resend/order/{order_id}",
    status_code=status.HTTP_200_OK,
    summary="Resend callback for an order (Admin)",
)
async def admin_resend_callback(
    order_id: int,
    admin_user: User = Depends(require_admin),
    order_service: OrderService = Depends(get_service(OrderService)),
    audit_service: AuditService = Depends(get_service(AuditService)),
):
    """
    Resend a webhook callback for a specific order by its internal ID.
    """
    # Verify order exists
    order = await order_service.repository.get(order_id)
    if not order:
        from app.core.exceptions import NotFoundException
        raise NotFoundException(f"Order {order_id} not found")
        
    send_order_callback.delay(order_id)
    
    await audit_service.log_action(
        action="resend_callback",
        entity_type="order",
        entity_id=str(order_id),
        user_id=str(admin_user.id),
    )
    
    return {"message": "Callback queued for resend"}


@router.post(
    "/resend/merchant/{merchant_id}",
    status_code=status.HTTP_200_OK,
    summary="Resend all callbacks for a merchant (Admin)",
)
async def admin_resend_merchant_callbacks(
    merchant_id: int,
    start_time: datetime,
    end_time: Optional[datetime] = None,
    admin_user: User = Depends(require_admin),
    order_service: OrderService = Depends(get_service(OrderService)),
    audit_service: AuditService = Depends(get_service(AuditService)),
):
    """
    Resend callbacks for all orders of a specific merchant within a time period.
    """
    orders = await order_service.get_orders_for_merchant_in_period(
        merchant_id=merchant_id,
        start_time=start_time,
        end_time=end_time
    )
    
    count = 0
    for order in orders:
        send_order_callback.delay(order.id)
        count += 1
        
    await audit_service.log_action(
        action="resend_merchant_callbacks",
        entity_type="merchant",
        entity_id=str(merchant_id),
        user_id=str(admin_user.id),
        new_values={"count": count, "start_time": start_time.isoformat(), "end_time": end_time.isoformat() if end_time else None},
    )
        
    return {"message": f"Queued {count} callbacks for resend"}
