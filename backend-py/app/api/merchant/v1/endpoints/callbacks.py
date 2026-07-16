from fastapi import APIRouter, Depends, HTTPException, status

from app.api.dependencies import get_service
from app.api.merchant.dependencies import get_current_merchant
from app.modules.merchants.models import Merchant
from app.modules.orders.service import OrderService
from app.workers.tasks.callbacks import send_order_callback

router = APIRouter()

@router.post(
    "/resend/order/{order_id}",
    status_code=status.HTTP_200_OK,
    summary="Resend callback for an order",
    description="Resend a webhook callback for a specific order by its external ID or UUID."
)
async def merchant_resend_callback(
    order_id: str,
    merchant: Merchant = Depends(get_current_merchant),
    order_service: OrderService = Depends(get_service(OrderService))
):
    """
    Resend a webhook callback for a specific order.
    The order_id can be either the UUID or the external_id.
    """
    import uuid
    from app.core.exceptions import NotFoundException
    
    order = None
    try:
        # Try to get by UUID first
        uuid.UUID(order_id)
        try:
            order = await order_service.get_merchant_order(merchant=merchant, order_id=order_id)
        except NotFoundException:
            pass
    except ValueError:
        pass
        
    if not order:
        # If not a valid UUID or not found by UUID, try external_id
        try:
            order = await order_service.get_merchant_order_by_external_id(merchant=merchant, external_id=order_id)
        except NotFoundException:
            pass
            
    if not order:
        raise NotFoundException("Order not found")
        
    send_order_callback.delay(order.id)
    return {"message": "Callback queued for resend"}