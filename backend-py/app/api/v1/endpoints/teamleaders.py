from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.modules.teamleaders.schemas import (
    TeamleadAdminItem,
    TeamleadLinkCreate,
    TeamleadLinkEnrichedResponse,
    TeamleadLinkResponse,
    TeamleadLinkUpdate,
    TeamleadRewardHistoryResponse,
    TeamleadStatsResponse,
    TeamleadTraderOrderFinanceResponse,
)
from app.modules.teamleaders.service import TeamleaderService
from app.modules.users.models import User
from app.modules.users.permissions import get_current_user, require_admin, require_teamlead

router = APIRouter()


# --- Admin Endpoints ---


@router.get(
    "/admin/teamleads",
    response_model=List[TeamleadAdminItem],
    dependencies=[Depends(require_admin)],
)
async def list_admin_teamleads(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = Query(None, description="Search by teamlead id or login"),
    is_active: Optional[bool] = Query(None),
    balance_from: Optional[float] = Query(None),
    balance_to: Optional[float] = Query(None),
    session: AsyncSession = Depends(get_db),
):
    """Admin: List all teamlead users with balances/filters."""
    service = TeamleaderService(session)
    return await service.list_admin_teamleads(
        skip=skip,
        limit=limit,
        search=search,
        is_active=is_active,
        balance_from=balance_from,
        balance_to=balance_to,
    )

@router.get(
    "/admin/links",
    response_model=List[TeamleadLinkResponse],
    dependencies=[Depends(require_admin)]
)
async def list_all_teamlead_links(
    skip: int = 0,
    limit: int = 100,
    teamlead_id: int = None,
    session: AsyncSession = Depends(get_db),
):
    """
    Admin: List all teamlead links.
    """
    service = TeamleaderService(session)
    return await service.list_all_links(skip=skip, limit=limit, teamlead_id=teamlead_id)


@router.post(
    "/admin/links", 
    response_model=TeamleadLinkResponse,
    dependencies=[Depends(require_admin)]
)
async def create_teamlead_link(
    data: TeamleadLinkCreate,
    admin_user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
):
    """
    Admin: Assign a teamlead to a merchant or trader with specific fee percentages.
    """
    service = TeamleaderService(session)
    return await service.create_link(data, admin_user_id=admin_user.id)


@router.patch(
    "/admin/links/{link_id}", 
    response_model=TeamleadLinkResponse,
    dependencies=[Depends(require_admin)]
)
async def update_teamlead_link(
    link_id: int,
    data: TeamleadLinkUpdate,
    admin_user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
):
    """
    Admin: Update commission percentages or status of a teamlead link.
    """
    service = TeamleaderService(session)
    return await service.update_link(link_id, data, admin_user_id=admin_user.id)


@router.delete(
    "/admin/links/{link_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
)
async def delete_teamlead_link(
    link_id: int,
    admin_user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
) -> None:
    """
    Admin: Remove a teamlead link entirely. Reward history is preserved.
    """
    service = TeamleaderService(session)
    await service.delete_link(link_id, admin_user_id=admin_user.id)


# --- Teamlead Endpoints ---

@router.get(
    "/my-links", 
    response_model=List[TeamleadLinkResponse],
    dependencies=[Depends(require_teamlead)]
)
async def get_my_links(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Teamlead: Get all active links (merchants and traders) assigned to the current teamlead.
    """
    service = TeamleaderService(session)
    return await service.get_teamlead_links(current_user.id)


@router.get(
    "/my-rewards", 
    response_model=List[TeamleadRewardHistoryResponse],
    dependencies=[Depends(require_teamlead)]
)
async def get_my_rewards(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Teamlead: Get the history of reward payouts.
    """
    service = TeamleaderService(session)
    return await service.get_teamlead_reward_history(current_user.id)


@router.get(
    "/my-trader-orders",
    response_model=List[TeamleadTraderOrderFinanceResponse],
    dependencies=[Depends(require_teamlead)],
)
async def get_my_trader_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Teamlead: successful orders from linked trader accounts with teamlead reward.
    """
    service = TeamleaderService(session)
    return await service.get_teamlead_trader_order_finances(
        current_user.id,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/my-links-enriched",
    response_model=List[TeamleadLinkEnrichedResponse],
    dependencies=[Depends(require_teamlead)],
)
async def get_my_links_enriched(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Teamlead: Linked users enriched with login and per-link income.
    """
    service = TeamleaderService(session)
    return await service.get_teamlead_links_enriched(current_user.id)


@router.get(
    "/my-stats",
    response_model=TeamleadStatsResponse,
    dependencies=[Depends(require_teamlead)],
)
async def get_my_stats(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Teamlead: Aggregated stats — total earned USDT and distinct orders count.
    """
    service = TeamleaderService(session)
    return await service.get_teamlead_stats(current_user.id)
