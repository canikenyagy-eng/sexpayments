from typing import List
from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_service
from app.api.merchant.dependencies import get_current_merchant
from app.modules.merchants.models import Merchant
from app.modules.rates.schemas import RateConfigResponse
from app.modules.rates.service import RateService

router = APIRouter()

# GET / Get current rates

@router.get(
    "/",
    response_model=List[RateConfigResponse],
    status_code=status.HTTP_200_OK,
    summary="Get current rates",
    description="Get a list of all active exchange rates",
)
async def get_active_rates(
    merchant: Merchant = Depends(get_current_merchant),
    rate_service: RateService = Depends(get_service(RateService)),
):
    return await rate_service.get_active_configs()
