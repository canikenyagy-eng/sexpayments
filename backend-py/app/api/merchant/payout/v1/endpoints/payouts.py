"""Merchant-facing payout API (authed by the payout terminal's own key).

Create a payout, check status, cancel while unclaimed. Responses are restricted
(``MerchantPayoutResponse``) — no trader/internal ids, no destination echo.
"""
from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_service
from app.api.merchant.payout.v1.dependencies import get_active_payout_terminal, get_payout_terminal
from app.core.exceptions import NotFoundException
from app.modules.payouts.models import Payout, PayoutTerminal
from app.modules.payouts.schemas.merchant import MerchantPayoutCreate, MerchantPayoutResponse
from app.modules.payouts.service import PayoutService

router = APIRouter()


def _resp(p: Payout) -> MerchantPayoutResponse:
    return MerchantPayoutResponse(
        id=str(p.uuid), external_id=p.external_id, amount=float(p.amount),
        currency=p.currency, status=p.status, created_at=p.created_at,
        expires_at=p.expires_at, completed_at=p.completed_at, canceled_at=p.canceled_at,
        rejection_reason=p.rejection_reason,
    )


@router.post("", response_model=MerchantPayoutResponse, status_code=status.HTTP_201_CREATED,
             summary="Create a payout")
async def create_payout(
    data: MerchantPayoutCreate,
    terminal: PayoutTerminal = Depends(get_active_payout_terminal),
    service: PayoutService = Depends(get_service(PayoutService)),
) -> MerchantPayoutResponse:
    payout = await service.create_payout(terminal.id, data, ttl_override_minutes=data.ttl_minutes)
    return _resp(payout)


@router.get("/{uuid}", response_model=MerchantPayoutResponse, summary="Get payout status")
async def get_payout(
    uuid: str,
    terminal: PayoutTerminal = Depends(get_payout_terminal),
    service: PayoutService = Depends(get_service(PayoutService)),
) -> MerchantPayoutResponse:
    payout = await service.repository.get_by_uuid_and_terminal(uuid, terminal.id)
    if not payout:
        raise NotFoundException("Payout not found")
    return _resp(payout)


@router.get("/external/{external_id}", response_model=MerchantPayoutResponse,
            summary="Get payout status by external id")
async def get_payout_by_external(
    external_id: str,
    terminal: PayoutTerminal = Depends(get_payout_terminal),
    service: PayoutService = Depends(get_service(PayoutService)),
) -> MerchantPayoutResponse:
    payout = await service.repository.get_by_external_and_terminal(external_id, terminal.id)
    if not payout:
        raise NotFoundException("Payout not found")
    return _resp(payout)


@router.post("/{uuid}/cancel", response_model=MerchantPayoutResponse,
             summary="Cancel an unclaimed payout")
async def cancel_payout(
    uuid: str,
    terminal: PayoutTerminal = Depends(get_payout_terminal),
    service: PayoutService = Depends(get_service(PayoutService)),
) -> MerchantPayoutResponse:
    payout = await service.cancel_by_merchant(uuid, terminal.id)
    return _resp(payout)
