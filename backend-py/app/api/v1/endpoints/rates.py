from typing import List
from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_service
from app.modules.rates.schemas import RateConfigCreate, RateConfigResponse, RateConfigUpdate
from app.modules.rates.service import RateService
from app.modules.users.models import User
from app.modules.users.permissions import require_admin

router = APIRouter()

@router.post(
    "/",
    response_model=RateConfigResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create rate config",
    description="Create a new rate configuration (e.g., Bybit P2P)",
)
async def create_rate_config(
    data: RateConfigCreate,
    admin_user: User = Depends(require_admin),
    rate_service: RateService = Depends(get_service(RateService)),
):
    return await rate_service.create_config(data, admin_user_id=admin_user.id)


@router.get(
    "/",
    response_model=List[RateConfigResponse],
    status_code=status.HTTP_200_OK,
    summary="List rate configs",
    description="Get all rate configurations",
    dependencies=[Depends(require_admin)],
)
async def list_rate_configs(
    rate_service: RateService = Depends(get_service(RateService)),
):
    return await rate_service.list_configs()


@router.get(
    "/{id}",
    response_model=RateConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Get rate config",
    description="Get a specific rate configuration",
    dependencies=[Depends(require_admin)],
)
async def get_rate_config(
    id: int,
    rate_service: RateService = Depends(get_service(RateService)),
):
    return await rate_service.get_config(id)


@router.patch(
    "/{id}",
    response_model=RateConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Update rate config",
    description="Update an existing rate configuration",
)
async def update_rate_config(
    id: int,
    data: RateConfigUpdate,
    admin_user: User = Depends(require_admin),
    rate_service: RateService = Depends(get_service(RateService)),
):
    return await rate_service.update_config(id, data, admin_user_id=admin_user.id)


@router.delete(
    "/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete rate config",
    description="Delete an existing rate configuration",
)
async def delete_rate_config(
    id: int,
    admin_user: User = Depends(require_admin),
    rate_service: RateService = Depends(get_service(RateService)),
):
    await rate_service.delete_config(id, admin_user_id=admin_user.id)
    return None


@router.post(
    "/{id}/sync",
    response_model=RateConfigResponse,
    status_code=status.HTTP_200_OK,
    summary="Sync rate",
    description="Force fetch and update the latest rate for a configuration",
    dependencies=[Depends(require_admin)],
)
async def sync_rate(
    id: int,
    rate_service: RateService = Depends(get_service(RateService)),
):
    await rate_service.update_rate(id)
    return await rate_service.get_config(id)
