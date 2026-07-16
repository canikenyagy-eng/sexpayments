from typing import Optional

from fastapi import APIRouter, Depends, Header

from app.api.bot.v1.dependencies import verify_bot_secret
from app.api.dependencies import get_service
from app.modules.base.schemas import BaseSchema
from app.modules.merchants.service import MerchantService


router = APIRouter()


class BotState(BaseSchema):
    telegram_user_id: int
    active_terminal_id: Optional[int] = None
    active_terminal_name: Optional[str] = None


class SetActiveTerminalRequest(BaseSchema):
    merchant_id: int


@router.get(
    "",
    response_model=BotState,
    dependencies=[Depends(verify_bot_secret)],
    summary="Get bot state for a Telegram user",
    description="Returns the currently active terminal (merchant) for the given TG user, if any.",
)
async def get_state(
    x_telegram_user_id: int = Header(..., alias="X-Telegram-User-Id"),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
) -> BotState:
    merchant = await merchant_service.get_active_terminal_for_tg(x_telegram_user_id)
    return BotState(
        telegram_user_id=x_telegram_user_id,
        active_terminal_id=merchant.id if merchant else None,
        active_terminal_name=merchant.name if merchant else None,
    )


@router.post(
    "/active-terminal",
    response_model=BotState,
    dependencies=[Depends(verify_bot_secret)],
    summary="Set active terminal for a Telegram user",
    description=(
        "Marks the given merchant as the active terminal for the TG user "
        "and unsets the flag on any other merchants where the user is listed."
    ),
)
async def set_active_terminal(
    payload: SetActiveTerminalRequest,
    x_telegram_user_id: int = Header(..., alias="X-Telegram-User-Id"),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
) -> BotState:
    merchant = await merchant_service.set_active_terminal_for_tg(
        tg_user_id=x_telegram_user_id,
        merchant_id=payload.merchant_id,
    )
    return BotState(
        telegram_user_id=x_telegram_user_id,
        active_terminal_id=merchant.id,
        active_terminal_name=merchant.name,
    )
