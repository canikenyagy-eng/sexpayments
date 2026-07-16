from typing import List

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_service
from app.modules.payments.schemas import PaymentOptionResponse
from app.modules.payments.service import PaymentOptionService
from app.modules.users.permissions import get_current_user

router = APIRouter()


@router.get(
    "/options",
    response_model=List[PaymentOptionResponse],
    status_code=status.HTTP_200_OK,
    summary="Get available payment options",
    description="Returns the list of active payment options (banks). Used by traders when creating requisites.",
    dependencies=[Depends(get_current_user)],
)
async def get_payment_options(
    payment_service: PaymentOptionService = Depends(get_service(PaymentOptionService)),
):
    return await payment_service.get_active_options()
