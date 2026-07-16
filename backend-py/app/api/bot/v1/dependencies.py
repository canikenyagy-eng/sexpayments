from fastapi import Depends, Header, Request

from app.api.dependencies import get_service
from app.core.config import get_settings
from app.core.exceptions import ForbiddenException, UnauthorizedException
from app.modules.merchants.models import Merchant
from app.modules.merchants.service import MerchantService, extract_telegram_ids


async def verify_bot_secret(
    x_bot_secret: str = Header(..., alias="X-Bot-Secret"),
) -> None:
    settings = get_settings()
    if not settings.MERCHANT_BOT_SECRET or x_bot_secret != settings.MERCHANT_BOT_SECRET:
        raise UnauthorizedException("Invalid bot secret")


async def verify_support_bot_secret(
    x_bot_secret: str = Header(..., alias="X-Bot-Secret"),
) -> None:
    """Auth for the support-bot ⇄ backend channel.

    Uses a distinct secret from MERCHANT_BOT_SECRET so the two bots can be
    rotated independently and a leak of one secret doesn't grant access to
    the other bot's endpoints.
    """
    settings = get_settings()
    if not settings.SUPPORT_BOT_SECRET or x_bot_secret != settings.SUPPORT_BOT_SECRET:
        raise UnauthorizedException("Invalid bot secret")


async def verify_merchant_dispute_bot_secret(
    x_bot_secret: str = Header(..., alias="X-Bot-Secret"),
) -> None:
    """Auth for the merchant-dispute-bot ⇄ backend channel.

    Distinct secret from the other bots so it can be rotated independently.
    Empty server-side secret rejects everything (bot effectively disabled).
    """
    settings = get_settings()
    if (
        not settings.MERCHANT_DISPUTE_BOT_SECRET
        or x_bot_secret != settings.MERCHANT_DISPUTE_BOT_SECRET
    ):
        raise UnauthorizedException("Invalid bot secret")


async def verify_trader_bot_secret(
    x_bot_secret: str = Header(..., alias="X-Bot-Secret"),
) -> None:
    """Auth for the trader-bot ⇄ backend channel (the 'оплачено' inline button).

    Reuses ``TRADER_BOT_SECRET`` — the symmetric secret the bot already knows
    (the backend uses it as outbound ``X-Bot-Secret`` when it pushes new-receipt
    notifications to the bot). Empty server-side secret rejects everything (the
    confirm button is then effectively disabled).
    """
    settings = get_settings()
    if not settings.TRADER_BOT_SECRET or x_bot_secret != settings.TRADER_BOT_SECRET:
        raise UnauthorizedException("Invalid bot secret")


async def verify_merchant_notify_bot_secret(
    x_bot_secret: str = Header(..., alias="X-Bot-Secret"),
) -> None:
    """Auth for the merchant-notify-bot ⇄ backend channel.

    Reuses ``MERCHANT_NOTIFY_BOT_SECRET`` — the symmetric secret the bot
    already knows (the backend uses it as outbound ``X-Bot-Secret`` when it
    pushes premoderation notifications to the bot). Empty server-side secret
    rejects everything (bot effectively disabled).
    """
    settings = get_settings()
    if (
        not settings.MERCHANT_NOTIFY_BOT_SECRET
        or x_bot_secret != settings.MERCHANT_NOTIFY_BOT_SECRET
    ):
        raise UnauthorizedException("Invalid bot secret")


async def get_merchant_for_bot(
    merchant_id: int,
    request: Request,
    x_telegram_user_id: int = Header(..., alias="X-Telegram-User-Id"),
    _: None = Depends(verify_bot_secret),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
) -> Merchant:
    """Resolve merchant and verify the Telegram user has access to it."""
    
    merchant = await merchant_service.get_by_id(merchant_id)
    tg_ids = extract_telegram_ids(merchant.telegram_user_ids)
    if x_telegram_user_id not in tg_ids:
        raise ForbiddenException("Telegram user is not authorized for this merchant")
    request.state.merchant_id = merchant.id
    return merchant
