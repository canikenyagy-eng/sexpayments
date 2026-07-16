from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.api.merchant.dependencies import get_current_merchant
from app.modules.finance.schemas import WithdrawalRequestCreate, WithdrawalRequestResponse
from app.modules.finance.service import FinanceService
from app.modules.merchants.models import Merchant

router = APIRouter()


@router.post(
    "/withdrawals/create",
    response_model=WithdrawalRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create withdrawal request",
)
async def create_withdrawal_request(
    data: WithdrawalRequestCreate,
    merchant: Merchant = Depends(get_current_merchant),
    session: AsyncSession = Depends(get_db),
):
    """
    Create a new withdrawal request.
    The requested amount plus the fixed withdrawal fee will be frozen (moved from WORK to ESCROW balance).
    """
    finance_service = FinanceService(session)
    return await finance_service.create_withdrawal_request(data, merchant=merchant)


@router.get(
    "/withdrawals/{uuid}",
    response_model=WithdrawalRequestResponse,
    summary="Get withdrawal request info",
)
async def get_withdrawal_request(
    uuid: str,
    merchant: Merchant = Depends(get_current_merchant),
    session: AsyncSession = Depends(get_db),
):
    """
    Get the details and status of a specific withdrawal request.
    """
    finance_service = FinanceService(session)
    return await finance_service.get_merchant_withdrawal_request(merchant, uuid)
