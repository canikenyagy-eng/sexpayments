from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.modules.clients.schemas.admin import (
    AdminClientBlockRequest,
    AdminClientOrderInfo,
    AdminClientResponse,
    AdminClientUnblockRequest,
)
from app.modules.clients.schemas.trader import TraderClientOrderInfo
from app.modules.clients.service import ClientService
from app.modules.users.models import User
from app.modules.users.permissions import get_current_user, require_admin, require_trader

router = APIRouter()


@router.get(
    "",
    response_model=List[AdminClientResponse],
    dependencies=[Depends(require_admin)],
    summary="List merchant clients (Admin)",
    description="Unique clients across merchants (materialised from order clientIDs).",
)
async def list_clients(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    merchant_id: Optional[int] = Query(None, description="Filter by merchant"),
    search: Optional[str] = Query(None, description="Search by clientID"),
    is_blocked: Optional[bool] = Query(None, description="Filter by ban state"),
    sort_by: Optional[str] = Query(
        None,
        description="Sort field: last_seen_at | first_seen_at | total_orders | successful_orders | turnover_usdt | conversion | blocked_attempts",
    ),
    sort_order: str = Query("desc", regex="^(asc|desc)$"),
    session: AsyncSession = Depends(get_db),
):
    service = ClientService(session)
    return await service.list_admin(
        merchant_id=merchant_id,
        client_search=search,
        is_blocked=is_blocked,
        sort_by=sort_by,
        sort_order=sort_order,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/for-order/{order_id}",
    response_model=Optional[AdminClientOrderInfo],
    dependencies=[Depends(require_admin)],
    summary="Client block for the admin order modal (Admin)",
    description=(
        "Our internal client public_id + all-time deals/conversion for an order, "
        "GATED by the merchant's unique_clients_enabled. Returns null when the "
        "toggle is off, the order has no client, or the client isn't materialised."
    ),
)
async def get_order_client(
    order_id: int,
    session: AsyncSession = Depends(get_db),
):
    service = ClientService(session)
    return await service.get_for_order(order_id)


@router.get(
    "/for-order/{order_id}/me",
    response_model=Optional[TraderClientOrderInfo],
    dependencies=[Depends(require_trader)],
    summary="Client block for the trader's own order modal (Trader)",
    description=(
        "Internal client public_id + all-time turnover/conversion for the trader's "
        "OWN order, GATED by the merchant's unique_clients_enabled. Returns null "
        "when the toggle is off, the order isn't the trader's, has no client, or "
        "the client isn't materialised."
    ),
)
async def get_order_client_me(
    order_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = ClientService(session)
    return await service.get_for_order_trader(order_id, current_user.id)


@router.post(
    "/block",
    response_model=AdminClientResponse,
    dependencies=[Depends(require_admin)],
    summary="Block a client (Admin)",
    description=(
        "Block by the natural key (merchant_id, client_user_id) — works from the "
        "Clients page and the order modal. The client gets no requisites; payin "
        "creation fails as a generic 'no requisite' (the ban is opaque to the merchant)."
    ),
)
async def block_client(
    data: AdminClientBlockRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = ClientService(session)
    return await service.block(
        admin_id=current_user.id,
        merchant_id=data.merchant_id,
        client_user_id=data.client_user_id,
        reason=data.reason,
    )


@router.post(
    "/unblock",
    response_model=AdminClientResponse,
    dependencies=[Depends(require_admin)],
    summary="Unblock a client (Admin)",
)
async def unblock_client(
    data: AdminClientUnblockRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = ClientService(session)
    return await service.unblock(
        admin_id=current_user.id,
        merchant_id=data.merchant_id,
        client_user_id=data.client_user_id,
    )
