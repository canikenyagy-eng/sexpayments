from typing import List

from fastapi import APIRouter, Depends, status

from app.api.dependencies import get_service
from app.modules.broadcasts.schemas import (
    BroadcastCreateRequest,
    BroadcastResponse,
    RecipientCountResponse,
)
from app.modules.broadcasts.service import BroadcastService
from app.modules.users.models import User
from app.modules.users.permissions import require_admin

router = APIRouter()


@router.post(
    "/",
    response_model=BroadcastResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send a broadcast to all traders (via the trader bot)",
    description=(
        "Creates a broadcast and enqueues the async send to every trader with a "
        "linked Telegram (audience: all | except_blocked). Returns the created "
        "record immediately; delivered/failed are filled in as the worker sends."
    ),
)
async def create_broadcast(
    data: BroadcastCreateRequest,
    admin_user: User = Depends(require_admin),
    service: BroadcastService = Depends(get_service(BroadcastService)),
):
    return await service.create_broadcast(data, admin_user_id=admin_user.id)


@router.get(
    "/",
    response_model=List[BroadcastResponse],
    summary="Broadcast history (most recent first)",
    dependencies=[Depends(require_admin)],
)
async def list_broadcasts(
    service: BroadcastService = Depends(get_service(BroadcastService)),
):
    return await service.list_recent()


@router.get(
    "/recipient-count",
    response_model=RecipientCountResponse,
    summary="Recipient counts per audience (for the compose hint/confirm)",
    dependencies=[Depends(require_admin)],
)
async def recipient_count(
    service: BroadcastService = Depends(get_service(BroadcastService)),
):
    return await service.recipient_counts()
