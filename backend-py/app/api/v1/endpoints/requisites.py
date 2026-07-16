from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_service
from app.modules.requisites.schemas import (
    RequisiteCreate,
    RequisiteResponse,
    RequisiteUpdate,
)
from app.modules.requisites.schemas.trader import (
    RequisiteTraderResponse,
    RequisiteTraderUpdate,
)
from app.modules.requisites.service import RequisiteService
from app.modules.users.models import User
from app.modules.users.permissions import get_current_user, require_admin, require_trader

router = APIRouter()

# --- TRADER SELF-SERVICE ---

@router.post(
    "/me",
    response_model=RequisiteTraderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a requisite",
    dependencies=[Depends(require_trader)],
)
async def create_my_requisite(
    data: RequisiteCreate,
    current_user: User = Depends(get_current_user),
    requisite_service: RequisiteService = Depends(get_service(RequisiteService)),
):
    """
    Create a new requisite for the currently authenticated trader.
    """
    return await requisite_service.create_requisite(current_user.id, data)


@router.get(
    "/me",
    response_model=List[RequisiteTraderResponse],
    summary="List my requisites",
    dependencies=[Depends(require_trader)],
)
async def list_my_requisites(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    nickname: Optional[str] = Query(None, description="Search by nickname / account holder / account number"),
    bank: Optional[str] = Query(None, description="Filter by bank name"),
    payment_method: Optional[str] = Query(None, description="Filter by payment method"),
    status: Optional[str] = Query(None, description="Filter by requisite status"),
    is_active: Optional[bool] = Query(None, description="Filter by is_active"),
    include_archived: bool = Query(False, description="Include archived requisites"),
    state: Optional[str] = Query(
        None,
        regex="^(active|disabled|blocked|archived)$",
        description="Availability state: active | disabled | blocked | archived",
    ),
    ready_only: bool = Query(False, description="Only requisites that can take a payin right now"),
    current_user: User = Depends(get_current_user),
    requisite_service: RequisiteService = Depends(get_service(RequisiteService)),
):
    """
    List all requisites for the currently authenticated trader.
    Archived requisites are hidden by default (pass include_archived=true or
    state=archived to show them).
    """
    requisites, _ = await requisite_service.get_trader_requisites(
        current_user.id,
        skip=skip,
        limit=limit,
        nickname=nickname,
        bank=bank,
        payment_method=payment_method,
        status=status,
        is_active=is_active,
        include_archived=include_archived,
        state=state,
        ready_only=ready_only,
    )
    return requisites


@router.get(
    "/me/{requisite_id}",
    response_model=RequisiteTraderResponse,
    summary="Get my requisite",
    dependencies=[Depends(require_trader)],
)
async def get_my_requisite(
    requisite_id: int,
    current_user: User = Depends(get_current_user),
    requisite_service: RequisiteService = Depends(get_service(RequisiteService)),
):
    """
    Get a specific requisite owned by the current trader.
    """
    return await requisite_service.get_trader_requisite(requisite_id, current_user.id)


@router.patch(
    "/me/{requisite_id}",
    response_model=RequisiteTraderResponse,
    summary="Update my requisite",
    dependencies=[Depends(require_trader)],
)
async def update_my_requisite(
    requisite_id: int,
    data: RequisiteTraderUpdate,
    current_user: User = Depends(get_current_user),
    requisite_service: RequisiteService = Depends(get_service(RequisiteService)),
):
    """
    Update a specific requisite owned by the current trader.
    """
    return await requisite_service.update_requisite(requisite_id, data, trader_id=current_user.id, user_id=current_user.id)


@router.post(
    "/me/{requisite_id}/enable",
    response_model=RequisiteTraderResponse,
    summary="Enable my requisite",
    dependencies=[Depends(require_trader)],
)
async def enable_my_requisite(
    requisite_id: int,
    current_user: User = Depends(get_current_user),
    requisite_service: RequisiteService = Depends(get_service(RequisiteService)),
):
    """Turn requisite ON (status = ENABLED). Trader self-service."""
    return await requisite_service.set_enabled(
        requisite_id, True, trader_id=current_user.id, user_id=current_user.id,
    )


@router.post(
    "/me/{requisite_id}/disable",
    response_model=RequisiteTraderResponse,
    summary="Disable my requisite",
    dependencies=[Depends(require_trader)],
)
async def disable_my_requisite(
    requisite_id: int,
    current_user: User = Depends(get_current_user),
    requisite_service: RequisiteService = Depends(get_service(RequisiteService)),
):
    """Turn requisite OFF (status = DISABLED). Trader self-service."""
    return await requisite_service.set_enabled(
        requisite_id, False, trader_id=current_user.id, user_id=current_user.id,
    )


@router.delete(
    "/me/{requisite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete my requisite",
    dependencies=[Depends(require_trader)],
)
async def delete_my_requisite(
    requisite_id: int,
    current_user: User = Depends(get_current_user),
    requisite_service: RequisiteService = Depends(get_service(RequisiteService)),
):
    """
    Delete (archive) a specific requisite owned by the current trader.
    """
    await requisite_service.delete_requisite(requisite_id, trader_id=current_user.id, user_id=current_user.id)


# --- ADMIN MANAGEMENT ---

@router.get(
    "/",
    response_model=List[RequisiteResponse],
    summary="List all requisites (Admin)",
    dependencies=[Depends(require_admin)],
)
async def list_all_requisites(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    trader_login: Optional[str] = Query(None, description="Filter by trader login"),
    payment_method: Optional[str] = Query(None, description="Filter by payment method"),
    is_active: Optional[bool] = Query(None, description="Filter by is_active"),
    is_enabled: Optional[bool] = Query(None, description="Filter by enabled status"),
    bank: Optional[str] = Query(None, description="Filter by bank name"),
    requisite_service: RequisiteService = Depends(get_service(RequisiteService)),
):
    """
    List all requisites in the system.
    """
    requisites, _ = await requisite_service.get_all_requisites(
        skip=skip,
        limit=limit,
        trader_login=trader_login,
        payment_method=payment_method,
        is_active=is_active,
        is_enabled=is_enabled,
        bank=bank,
    )
    return requisites


@router.get(
    "/{requisite_id}",
    response_model=RequisiteResponse,
    summary="Get any requisite (Admin)",
    dependencies=[Depends(require_admin)],
)
async def get_any_requisite(
    requisite_id: int,
    requisite_service: RequisiteService = Depends(get_service(RequisiteService)),
):
    """
    Get a specific requisite by ID.
    """
    return await requisite_service.get_requisite(requisite_id)


@router.patch(
    "/{requisite_id}",
    response_model=RequisiteResponse,
    summary="Update any requisite (Admin)",
    dependencies=[Depends(require_admin)],
)
async def update_any_requisite(
    requisite_id: int,
    data: RequisiteUpdate,
    admin_user: User = Depends(require_admin),
    requisite_service: RequisiteService = Depends(get_service(RequisiteService)),
):
    """
    Update any requisite in the system.
    """
    return await requisite_service.update_requisite(requisite_id, data, user_id=admin_user.id)


@router.delete(
    "/{requisite_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete any requisite (Admin)",
    dependencies=[Depends(require_admin)],
)
async def delete_any_requisite(
    requisite_id: int,
    admin_user: User = Depends(require_admin),
    requisite_service: RequisiteService = Depends(get_service(RequisiteService)),
):
    """
    Delete (archive) any requisite in the system.
    """
    await requisite_service.delete_requisite(requisite_id, user_id=admin_user.id)
