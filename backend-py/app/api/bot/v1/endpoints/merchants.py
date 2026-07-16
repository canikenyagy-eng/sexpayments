from typing import List

from fastapi import APIRouter, Depends, Header

from app.api.bot.v1.dependencies import verify_bot_secret
from app.api.dependencies import get_service
from app.modules.finance.service import FinanceService
from app.modules.merchants.schemas import (
    BotBalancesResponse,
    BotLimitsResponse,
    BotMerchantItem,
)
from app.modules.merchants.service import MerchantService

router = APIRouter()


@router.get(
    "",
    response_model=List[BotMerchantItem],
    dependencies=[Depends(verify_bot_secret)],
    summary="List merchants for a Telegram user",
    description="Returns all merchant profiles where the given Telegram user ID is in telegram_user_ids",
)
async def list_merchants_for_tg_user(
    x_telegram_user_id: int = Header(..., alias="X-Telegram-User-Id"),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
) -> List[BotMerchantItem]:
    merchants = await merchant_service.list_by_telegram_user_id(x_telegram_user_id)
    return [
        BotMerchantItem(
            id=m.id,
            name=m.name,
            status=m.status.value,
            currency=m.currency.value,
        )
        for m in merchants
    ]


@router.get(
    "/balances",
    response_model=BotBalancesResponse,
    dependencies=[Depends(verify_bot_secret)],
    summary="Aggregated balance + per-terminal breakdown for a Telegram user",
)
async def get_balances_for_tg_user(
    x_telegram_user_id: int = Header(..., alias="X-Telegram-User-Id"),
    finance_service: FinanceService = Depends(get_service(FinanceService)),
) -> BotBalancesResponse:
    return await finance_service.get_bot_balances_for_tg_user(x_telegram_user_id)


@router.get(
    "/limits",
    response_model=BotLimitsResponse,
    dependencies=[Depends(verify_bot_secret)],
    summary="Per-terminal payin capacity (limits) for a Telegram user",
    description=(
        "For every merchant terminal owned by the TG user, returns aggregated "
        "payin capacity per payment method: remaining daily / monthly RUB "
        "headroom (already minus in-flight orders), min/max amount per single "
        "order, and free concurrent-order slots."
    ),
)
async def get_limits_for_tg_user(
    x_telegram_user_id: int = Header(..., alias="X-Telegram-User-Id"),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
) -> BotLimitsResponse:
    return await merchant_service.get_bot_limits_for_tg_user(x_telegram_user_id)
