"""Bot-channel endpoints for ``merchant-notify-bot``.

The bot lives in a Telegram group bound to a merchant via
``merchants.notify_telegram_group_id``. Beyond receiving outbound push messages
from the backend (PDF/video re-upload requests, etc.), it now serves inbound
operator commands like ``/limit`` — those queries hit the endpoints here.

Auth: shared ``X-Bot-Secret`` == ``MERCHANT_NOTIFY_BOT_SECRET``. The chat→merchant
binding is the trust boundary — any chat member can post commands.

Endpoints:
  * GET /limits?chat_id=…
      Resolves the merchant by chat_id, then asks ``PoolingService`` for the
      min/max fiat amount the platform can accept right now for each payment
      method, honouring every pooling gate (active local requisites, daily /
      monthly remaining, trader balance, merchant ACL). Used to answer the
      bot's ``/limit`` command.
"""
from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select

from app.api.bot.v1.dependencies import verify_merchant_notify_bot_secret
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.core.exceptions import NotFoundException, ValidationException
from app.core.logging import get_logger
from app.api.dependencies import get_db
from app.modules.merchants.models import Merchant
from app.modules.pooling.service import PoolingService
from app.modules.rates.service import RateService

logger = get_logger(__name__)

router = APIRouter()


class AmountRange(BaseModel):
    """One contiguous fiat range accepted by ≥1 requisite right now."""
    min: float
    max: float


class MethodLimit(BaseModel):
    """All accepting ranges for a single payment method, ordered ascending.
    Empty list ⇒ no requisite can accept any amount (all out of balance/limit).
    Multiple entries ⇒ there are gaps — the merchant should see them as-is so
    they understand "between A.max and B.min nobody can take the order"."""
    ranges: List[AmountRange]


class LimitsResponse(BaseModel):
    """Per-method limits the bot reports via ``/limit``."""
    currency: str
    sbp: MethodLimit
    card: MethodLimit


async def _rate_for(session, merchant: Merchant) -> Decimal:
    """Pick the current fiat→USDT rate the platform would use for this merchant
    — mirrors PayoutService._rate_for so the bot's max calc matches what payin
    would actually accept."""
    rate_service = RateService(session)
    config = None
    if merchant.rate_config_id:
        try:
            config = await rate_service.get_config(merchant.rate_config_id)
            if not config.is_active or not config.current_rate:
                config = None
        except Exception:  # noqa: BLE001
            config = None
    if config is None:
        active = await rate_service.get_active_configs()
        config = next((c for c in active if c.fiat_currency == merchant.currency), None)
    if not config or not config.current_rate:
        raise ValidationException(f"No active rate for {merchant.currency.value}")
    return Decimal(str(config.current_rate))


@router.get(
    "/limits",
    response_model=LimitsResponse,
    dependencies=[Depends(verify_merchant_notify_bot_secret)],
    summary="Min/max payin amount per method for the bound merchant",
)
async def get_limits(
    chat_id: int = Query(..., description="Telegram chat_id (notify-group)"),
    session=Depends(get_db),
) -> LimitsResponse:
    merchant = (
        await session.execute(
            select(Merchant).where(Merchant.notify_telegram_group_id == chat_id)
        )
    ).scalar_one_or_none()
    if merchant is None:
        raise NotFoundException(f"No merchant bound to chat {chat_id}")

    rate = await _rate_for(session, merchant)
    pooling = PoolingService(session)

    async def _for(method: PaymentMethod) -> MethodLimit:
        ranges = await pooling.compute_method_limits(
            merchant_id=merchant.id,
            currency=merchant.currency,
            payment_method=method,
            rate=rate,
        )
        return MethodLimit(ranges=[
            AmountRange(min=float(r["min"]), max=float(r["max"])) for r in ranges
        ])

    return LimitsResponse(
        currency=merchant.currency.value if isinstance(merchant.currency, Currency) else str(merchant.currency),
        sbp=await _for(PaymentMethod.SBP),
        card=await _for(PaymentMethod.CARD),
    )
