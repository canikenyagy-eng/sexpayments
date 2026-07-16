from typing import List, Optional

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile, status

from app.common.enums.receipts import ReceiptUploader
from app.api.bot.v1.dependencies import get_merchant_for_bot, verify_bot_secret
from app.api.dependencies import get_service
from app.common.enums.orders import ACTIVE_STATUSES, FINAL_STATUSES, OrderSource
from app.modules.merchants.models import Merchant
from app.modules.orders.schemas import MerchantOrderResponse, MerchantPayinCreate, MerchantRequisiteInfo
from app.modules.orders.service import OrderService
from app.modules.base.schemas import BaseSchema

router = APIRouter()


class BotActiveOrder(BaseSchema):
    id: str
    internal_id: Optional[str] = None
    merchant_id: int
    status: str
    is_final: bool


def _build_order_response(
    order,
    merchant: Merchant,
    *,
    request: Optional[Request] = None,
) -> MerchantOrderResponse:
    # Stash the integer order_id for MerchantApiLoggingMiddleware so it
    # doesn't have to re-resolve from the UUID in the response body.
    if request is not None and order is not None:
        request.state.order_id = order.id
    req_info = None
    requisite = order.requisite
    if requisite:
        option = order.payment_option
        bank_name = (option.name if option else None) or requisite.bank_name
        req_info = MerchantRequisiteInfo(
            bank_name=bank_name,
            account_number=requisite.account_number,
            account_holder=requisite.account_holder,
            payment_method=requisite.payment_method,
            currency=requisite.currency,
            payment_option_code=option.code if option else None,
            payment_option_name=option.name if option else None,
        )
    return MerchantOrderResponse(
        id=str(order.uuid),
        internalId=order.external_id,
        userId=order.client_user_id,
        merchant_name=merchant.name,
        amount=float(order.amount),
        amount_usdt=float(order.amount_usdt) if order.amount_usdt is not None else None,
        fee_usdt=float(order.fee_usdt) if order.fee_usdt is not None else None,
        exchange_rate=float(order.exchange_rate) if order.exchange_rate is not None else None,
        currency=order.currency,
        status=order.status,
        payment_url=order.payment_url,
        created_at=order.created_at,
        expires_at=order.date_end,
        requisite=req_info,
    )


@router.post(
    "",
    response_model=MerchantOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create payin order (bot)",
)
async def create_payin(
    data: MerchantPayinCreate,
    request: Request,
    merchant: Merchant = Depends(get_merchant_for_bot),
    order_service: OrderService = Depends(get_service(OrderService)),
) -> MerchantOrderResponse:
    order = await order_service.create_payin_order(merchant=merchant, data=data, source=OrderSource.BOT)
    return _build_order_response(order, merchant, request=request)


@router.get(
    "/{order_id}",
    response_model=MerchantOrderResponse,
    summary="Get order status (bot)",
)
async def get_order(
    order_id: str,
    request: Request,
    merchant: Merchant = Depends(get_merchant_for_bot),
    order_service: OrderService = Depends(get_service(OrderService)),
) -> MerchantOrderResponse:
    order = await order_service.get_merchant_order(merchant=merchant, order_id=order_id)
    return _build_order_response(order, merchant, request=request)


@router.post(
    "/{order_id}/cancel",
    response_model=MerchantOrderResponse,
    summary="Cancel order (bot)",
)
async def cancel_order(
    order_id: str,
    request: Request,
    merchant: Merchant = Depends(get_merchant_for_bot),
    order_service: OrderService = Depends(get_service(OrderService)),
) -> MerchantOrderResponse:
    order = await order_service.cancel_order(merchant=merchant, order_id=order_id)
    return _build_order_response(order, merchant, request=request)


@router.post(
    "/{order_id}/confirm",
    response_model=MerchantOrderResponse,
    summary="Confirm transfer / upload receipt (bot)",
)
async def confirm_order(
    order_id: str,
    request: Request,
    attachment: UploadFile = File(..., description="Receipt file (max 10MB)"),
    merchant: Merchant = Depends(get_merchant_for_bot),
    order_service: OrderService = Depends(get_service(OrderService)),
) -> MerchantOrderResponse:
    order = await order_service.confirm_order(
        merchant=merchant, attachment=attachment, order_id=order_id, uploaded_by=ReceiptUploader.MERCHANT
    )
    return _build_order_response(order, merchant, request=request)


@router.get(
    "",
    response_model=List[BotActiveOrder],
    summary="List active orders for bot recovery on startup",
    description=(
        "Returns all non-final orders for this merchant that were created by the bot "
        "(internalId starts with 'tg_'). Used to restore the status tracker after restart."
    ),
)
async def list_active_orders(
    merchant: Merchant = Depends(get_merchant_for_bot),
    order_service: OrderService = Depends(get_service(OrderService)),
) -> List[BotActiveOrder]:
    orders = await order_service.list_active_bot_orders(merchant.id)
    return [
        BotActiveOrder(
            id=str(o.uuid),
            internal_id=o.external_id,
            merchant_id=merchant.id,
            status=o.status.value,
            is_final=o.status in FINAL_STATUSES,
        )
        for o in orders
    ]
