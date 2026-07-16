from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import get_service
from app.modules.achievements.schemas.trader import TraderAchievementsResponse
from app.modules.achievements.service import AchievementService
from app.modules.traders.schemas import (
    TraderDefaultProviderRequest,
    TraderGroupCreate,
    TraderGroupResponse,
    TraderGroupUpdate,
    TraderReceiptAutoCheckRequest,
    TraderResponse,
    TraderUpdateAdmin,
    TraderToggleRequest,
)
from app.modules.traders.schemas.trader import TraderMeResponse
from app.modules.traders.service import TraderService
from app.modules.users.models import User
from app.modules.users.permissions import get_current_user, require_admin, require_trader

router = APIRouter()

# --- TRADER SELF-SERVICE ---

@router.get(
    "/me",
    response_model=TraderMeResponse,
    summary="Get current trader profile",
    dependencies=[Depends(require_trader)],
)
async def get_my_trader_profile(
    current_user: User = Depends(get_current_user),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    """
    Get the trader profile for the currently authenticated user.
    """
    return await trader_service.get_or_create_trader(current_user.id)


@router.get(
    "/me/achievements",
    response_model=TraderAchievementsResponse,
    summary="Current trader's achievements: total bonus, today's turnover, breakdown",
    dependencies=[Depends(require_trader)],
)
async def get_my_achievements(
    current_user: User = Depends(get_current_user),
    service: AchievementService = Depends(get_service(AchievementService)),
):
    """Get the current trader's achievements: total bonus, today's turnover, per-rule breakdown."""
    view = await service.get_trader_view(current_user.id)
    return TraderAchievementsResponse(**view)


@router.post(
    "/achievements/recompute",
    dependencies=[Depends(require_admin)],
    summary="Force-recompute all trader achievement bonuses now (admin/ops)",
)
async def recompute_achievements(
    service: AchievementService = Depends(get_service(AchievementService)),
):
    changed = await service.recompute_now()
    return {"changed": changed}


@router.patch(
    "/me/payin",
    response_model=TraderMeResponse,
    summary="Toggle payin status",
    dependencies=[Depends(require_trader)],
)
async def toggle_payin_status(
    data: TraderToggleRequest,
    current_user: User = Depends(get_current_user),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    """
    Enable or disable payin processing for the current trader.
    """
    return await trader_service.toggle_payin(current_user.id, data.is_active)


@router.patch(
    "/me/payout",
    response_model=TraderMeResponse,
    summary="Toggle payout status",
    dependencies=[Depends(require_trader)],
)
async def toggle_payout_status(
    data: TraderToggleRequest,
    current_user: User = Depends(get_current_user),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    """
    Enable or disable payout processing for the current trader.
    """
    return await trader_service.toggle_payout(current_user.id, data.is_active)


@router.patch(
    "/me/receipt-auto-check",
    response_model=TraderMeResponse,
    summary="Toggle automatic receipt verification for the current trader",
    dependencies=[Depends(require_trader)],
)
async def toggle_receipt_auto_check(
    data: TraderReceiptAutoCheckRequest,
    current_user: User = Depends(get_current_user),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    """
    When enabled, every receipt uploaded by a merchant on this trader's
    orders is automatically run through the active receipt-check provider.
    Trader WORK balance is charged the configured per-check price.
    """
    return await trader_service.toggle_receipt_auto_check(current_user.id, data.enabled)


@router.patch(
    "/me/receipt-check-provider",
    response_model=TraderMeResponse,
    summary="Set the trader's default receipt-check provider",
    dependencies=[Depends(require_trader)],
)
async def set_default_receipt_provider(
    data: TraderDefaultProviderRequest,
    current_user: User = Depends(get_current_user),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    """
    Set (or clear with ``provider_id: null``) the provider pre-selected in the
    manual check modal and used for automatic checks. Must reference an active
    provider.
    """
    return await trader_service.set_default_receipt_provider(
        current_user.id, data.provider_id
    )


# --- ADMIN TRADER MANAGEMENT ---
# Важно: статические сегменты (/groups) объявлять ДО /{trader_id}, иначе FastAPI
# сопоставит "groups" с целочисленным параметром и вернёт 422.

@router.get(
    "/",
    response_model=List[TraderResponse],
    summary="List all traders (Admin)",
    dependencies=[Depends(require_admin)],
)
async def list_traders(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    search: Optional[str] = Query(None, description="Search by ID or username"),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    traders, _ = await trader_service.get_traders(skip=skip, limit=limit, search=search)
    return traders


# --- ADMIN GROUP MANAGEMENT ---

@router.post(
    "/groups",
    response_model=TraderGroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create trader group (Admin)",
    dependencies=[Depends(require_admin)],
)
async def create_trader_group(
    data: TraderGroupCreate,
    admin_user: User = Depends(require_admin),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    """
    Create a new group for traders.
    """
    return await trader_service.create_group(data, admin_user_id=admin_user.id)


@router.get(
    "/groups",
    response_model=List[TraderGroupResponse],
    summary="List trader groups (Admin)",
    dependencies=[Depends(require_admin)],
)
async def list_trader_groups(
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    """
    List all trader groups.
    """
    return await trader_service.get_groups()


@router.patch(
    "/groups/{group_id}",
    response_model=TraderGroupResponse,
    summary="Update trader group (Admin)",
    dependencies=[Depends(require_admin)],
)
async def update_trader_group(
    group_id: int,
    data: TraderGroupUpdate,
    admin_user: User = Depends(require_admin),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    """
    Update a trader group's details.
    """
    return await trader_service.update_group(group_id, data, admin_user_id=admin_user.id)


@router.post(
    "/groups/{group_id}/traders/{trader_id}",
    response_model=TraderGroupResponse,
    summary="Add trader to group (Admin)",
    dependencies=[Depends(require_admin)],
)
async def add_trader_to_group(
    group_id: int,
    trader_id: int,
    admin_user: User = Depends(require_admin),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    """
    Link a trader to a specific group.
    """
    return await trader_service.add_trader_to_group(group_id, trader_id, admin_user_id=admin_user.id)


@router.post(
    "/groups/{group_id}/merchants/{merchant_id}",
    response_model=TraderGroupResponse,
    summary="Add merchant to group (Admin)",
    dependencies=[Depends(require_admin)],
)
async def add_merchant_to_group(
    group_id: int,
    merchant_id: int,
    admin_user: User = Depends(require_admin),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    """
    Link a merchant to a specific group. Merchants will only use traders from their linked groups.
    """
    return await trader_service.add_merchant_to_group(group_id, merchant_id, admin_user_id=admin_user.id)


@router.delete(
    "/groups/{group_id}/traders/{trader_id}",
    response_model=TraderGroupResponse,
    summary="Remove trader from group (Admin)",
    dependencies=[Depends(require_admin)],
)
async def remove_trader_from_group(
    group_id: int,
    trader_id: int,
    admin_user: User = Depends(require_admin),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    return await trader_service.remove_trader_from_group(group_id, trader_id, admin_user_id=admin_user.id)


@router.delete(
    "/groups/{group_id}/merchants/{merchant_id}",
    response_model=TraderGroupResponse,
    summary="Remove merchant from group (Admin)",
    dependencies=[Depends(require_admin)],
)
async def remove_merchant_from_group(
    group_id: int,
    merchant_id: int,
    admin_user: User = Depends(require_admin),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    return await trader_service.remove_merchant_from_group(group_id, merchant_id, admin_user_id=admin_user.id)


@router.delete(
    "/groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete trader group (Admin)",
    dependencies=[Depends(require_admin)],
)
async def delete_trader_group(
    group_id: int,
    admin_user: User = Depends(require_admin),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    await trader_service.delete_group(group_id, admin_user_id=admin_user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{trader_id}",
    response_model=TraderResponse,
    summary="Get trader by ID (Admin)",
    dependencies=[Depends(require_admin)],
)
async def get_trader(
    trader_id: int,
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    """
    Get a specific trader by their ID.
    """
    return await trader_service.get_trader_by_id(trader_id)


@router.patch(
    "/{trader_id}",
    response_model=TraderResponse,
    summary="Update trader (Admin)",
    dependencies=[Depends(require_admin)],
)
async def update_trader_admin(
    trader_id: int,
    data: TraderUpdateAdmin,
    admin_user: User = Depends(require_admin),
    trader_service: TraderService = Depends(get_service(TraderService)),
):
    """
    Update a trader's profile. Admins can update status and methods_config (fees, limits).
    """
    return await trader_service.update_trader_admin(trader_id, data, admin_user_id=admin_user.id)
