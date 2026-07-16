import mimetypes
import os
from typing import Optional

from fastapi import APIRouter, Depends, File, Request, UploadFile, status
from fastapi.responses import FileResponse

from app.common.enums.receipts import ReceiptUploader
from app.api.dependencies import get_service
from app.api.merchant.dependencies import get_active_merchant, get_current_merchant
from app.common.enums.orders import OrderSource
from app.core.exceptions import NotFoundException
from app.modules.merchants.models import Merchant
from app.modules.orders.schemas import MerchantOrderResponse, MerchantPayinCreate, MerchantRequisiteInfo
from app.modules.orders.service import OrderService

router = APIRouter()


async def _build_order_response(
    order,
    order_service: OrderService,
    merchant: Merchant,
    *,
    merchant_name: Optional[str] = None,
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
        merchant_name=merchant_name if merchant_name is not None else merchant.name,
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
    "/payin",
    response_model=MerchantOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payin order",
    description="Create a new payin transaction for the merchant",
)
async def create_payin(
    data: MerchantPayinCreate,
    request: Request,
    merchant: Merchant = Depends(get_active_merchant),
    order_service: OrderService = Depends(get_service(OrderService)),
):
    # Read merchant attributes before the service call to avoid potential
    # identity-map expiry issues after begin_nested() releases the savepoint.
    merchant_id = merchant.id
    merchant_name = merchant.name
    order = await order_service.create_payin_order(merchant=merchant, data=data, source=OrderSource.API)
    return await _build_order_response(order, order_service, merchant, merchant_name=merchant_name, request=request)


@router.get(
    "/{id}",
    response_model=MerchantOrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Get order info",
    description="Get current information about a specific order",
)
async def get_order(
    id: str,
    request: Request,
    merchant: Merchant = Depends(get_current_merchant),
    order_service: OrderService = Depends(get_service(OrderService)),
):
    order = await order_service.get_merchant_order(merchant=merchant, order_id=id)
    return await _build_order_response(order, order_service, merchant, request=request)


@router.post(
    "/{id}/confirm-transfer",
    response_model=MerchantOrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm transfer",
    description="Upload a receipt to confirm the transfer for an order",
)
async def confirm_transfer(
    id: str,
    request: Request,
    attachment: UploadFile = File(..., description="Receipt file (max 10MB)"),
    merchant: Merchant = Depends(get_current_merchant),
    order_service: OrderService = Depends(get_service(OrderService)),
):
    order = await order_service.confirm_order(
        merchant=merchant, attachment=attachment, order_id=id, uploaded_by=ReceiptUploader.MERCHANT
    )
    return await _build_order_response(order, order_service, merchant, request=request)


@router.get(
    "/external/{external_id}",
    response_model=MerchantOrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Get order info by external ID",
    description="Get current information about a specific order using the merchant's internal ID",
)
async def get_order_by_external_id(
    external_id: str,
    request: Request,
    merchant: Merchant = Depends(get_current_merchant),
    order_service: OrderService = Depends(get_service(OrderService)),
):
    order = await order_service.get_merchant_order_by_external_id(merchant=merchant, external_id=external_id)
    return await _build_order_response(order, order_service, merchant, request=request)


@router.post(
    "/external/{external_id}/confirm-transfer",
    response_model=MerchantOrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Confirm transfer by external ID",
    description="Upload a receipt to confirm the transfer for an order using the merchant's internal ID",
)
async def confirm_transfer_by_external_id(
    external_id: str,
    request: Request,
    attachment: UploadFile = File(..., description="Receipt file (max 10MB)"),
    merchant: Merchant = Depends(get_current_merchant),
    order_service: OrderService = Depends(get_service(OrderService)),
):
    order = await order_service.confirm_order(
        merchant=merchant, attachment=attachment, external_id=external_id, uploaded_by=ReceiptUploader.MERCHANT
    )
    return await _build_order_response(order, order_service, merchant, request=request)


@router.get(
    "/{id}/receipt",
    summary="Download receipt for order",
    response_class=FileResponse,
)
async def download_receipt(
    id: str,
    request: Request,
    merchant: Merchant = Depends(get_current_merchant),
    order_service: OrderService = Depends(get_service(OrderService)),
):
    order = await order_service.repository.get_by_uuid_and_merchant(id, merchant.id)
    if not order:
        raise NotFoundException(f"Order {id} not found")
    request.state.order_id = order.id
    if not order.receipt_file:
        raise NotFoundException("No receipt uploaded for this order")
    if not os.path.exists(order.receipt_file):
        raise NotFoundException("Receipt file not found on disk")
    media_type, _ = mimetypes.guess_type(order.receipt_file)
    filename = os.path.basename(order.receipt_file)
    return FileResponse(
        path=order.receipt_file,
        media_type=media_type or "application/octet-stream",
        filename=filename,
    )


@router.post(
    "/{id}/cancel",
    response_model=MerchantOrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel order",
    description="Cancel a specific order before it is completed",
)
async def cancel_order(
    id: str,
    request: Request,
    merchant: Merchant = Depends(get_current_merchant),
    order_service: OrderService = Depends(get_service(OrderService)),
):
    order = await order_service.cancel_order(merchant=merchant, order_id=id)
    return await _build_order_response(order, order_service, merchant, request=request)


@router.post(
    "/external/{external_id}/cancel",
    response_model=MerchantOrderResponse,
    status_code=status.HTTP_200_OK,
    summary="Cancel order by external ID",
    description="Cancel a specific order using the merchant's internal ID before it is completed",
)
async def cancel_order_by_external_id(
    external_id: str,
    request: Request,
    merchant: Merchant = Depends(get_current_merchant),
    order_service: OrderService = Depends(get_service(OrderService)),
):
    order = await order_service.cancel_order(merchant=merchant, external_id=external_id)
    return await _build_order_response(order, order_service, merchant, request=request)
