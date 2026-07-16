from typing import List
from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_service
from app.api.merchant.dependencies import get_current_merchant
from app.common.enums.payments import PaymentMethod
from app.modules.merchants.models import Merchant
from app.modules.payments.schemas import MerchantPaymentOptionResponse
from app.modules.payments.service import PaymentOptionService

router = APIRouter()

# GET /methods Get payment methods
#
# GET /options Get payment options

@router.get(
    "/methods",
    response_model=List[str],
    status_code=status.HTTP_200_OK,
    summary="Get payment methods",
    description="Get a list of all supported payment methods",
)
async def get_payment_methods(
    merchant: Merchant = Depends(get_current_merchant),
):
    return [method.value for method in PaymentMethod]


@router.get(
    "/options",
    response_model=List[MerchantPaymentOptionResponse],
    status_code=status.HTTP_200_OK,
    summary="Get payment options",
    description="Get a list of all active payment options (banks, providers)",
)
async def get_payment_options(
    merchant: Merchant = Depends(get_current_merchant),
    payment_service: PaymentOptionService = Depends(get_service(PaymentOptionService)),
):
    return await payment_service.get_active_options()
