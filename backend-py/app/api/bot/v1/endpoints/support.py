"""Bot-channel endpoints used by support-bot.

Only one endpoint for now — the moderation decision callback. The bot
posts here when an admin clicks one of the inline-keyboard buttons.

Side-effect orchestration lives here (not in ReceiptModerationService)
so the service stays pure and unit-testable. Keep this thin: validate,
look up the order, hand to the service, then fan out to celery tasks.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy import select

from app.api.bot.v1.dependencies import verify_support_bot_secret
from app.api.dependencies import get_service
from app.core.exceptions import NotFoundException
from app.modules.orders.models import Order
from app.modules.receipts.schemas.bot import (
    SupportBotModerationDecisionRequest,
    SupportBotModerationResponse,
)
from app.modules.receipts.moderation import ReceiptModerationService
from app.modules.receipts.moderation.coordinator import apply_moderation_decision

router = APIRouter()


@router.post(
    "/orders/{order_uuid}/moderate",
    response_model=SupportBotModerationResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_support_bot_secret)],
    summary="Apply a moderation decision posted by support-bot",
)
async def moderate_order(
    order_uuid: UUID,
    payload: SupportBotModerationDecisionRequest,
    moderation_service: ReceiptModerationService = Depends(
        get_service(ReceiptModerationService)
    ),
):
    session = moderation_service.session

    order = (
        await session.execute(select(Order).where(Order.uuid == order_uuid))
    ).scalar_one_or_none()
    if order is None:
        raise NotFoundException(f"Order {order_uuid} not found")

    row = await apply_moderation_decision(
        session=session,
        order=order,
        decision=payload.decision,
        moderator_tg_id=payload.moderator_tg_id,
        moderator_username=payload.moderator_username,
        message_id=payload.message_id,
    )

    return SupportBotModerationResponse(
        order_uuid=str(order.uuid),
        moderation_status=order.moderation_status,
        decision=row.decision,
    )
