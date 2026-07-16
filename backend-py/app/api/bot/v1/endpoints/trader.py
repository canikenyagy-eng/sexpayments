"""Bot-channel endpoints used by the trader-bot.

One endpoint: the trader confirms an order as paid ('оплачено') by tapping the
inline button under the receipt the bot posted into their Telegram group. This
runs the SAME settlement as the trader-cabinet ``POST /orders/{id}/success``.

Auth is the symmetric ``TRADER_BOT_SECRET`` (``verify_trader_bot_secret``).
Authorisation that the group may confirm THIS order is group-based and lives in
``OrderService.complete_order_from_trader_group`` (kept out of the API layer).
"""
from typing import List, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, status

from app.api.bot.v1.dependencies import verify_trader_bot_secret
from app.api.dependencies import get_service
from app.core.exceptions import ConflictException, ValidationException
from app.core.logging import get_logger
from app.modules.base.schemas import BaseSchema
from app.modules.disputes.service import DisputeService
from app.modules.orders.service import OrderService
from app.modules.receipt_checks.service import ReceiptCheckService

router = APIRouter()


class TraderBotConfirmRequest(BaseSchema):
    telegram_group_id: int
    telegram_user_id: Optional[int] = None
    telegram_username: Optional[str] = None


class TraderBotConfirmResponse(BaseSchema):
    order_uuid: str
    status: str


class TraderBotRequestProofRequest(BaseSchema):
    telegram_group_id: int
    kind: str  # "video" | "pdf"
    telegram_user_id: Optional[int] = None
    telegram_username: Optional[str] = None


class TraderBotRequestProofResponse(BaseSchema):
    order_uuid: str
    dispute_uuid: str
    substatus: Optional[str] = None


@router.post(
    "/orders/{order_uuid}/confirm",
    response_model=TraderBotConfirmResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_trader_bot_secret)],
    summary="Trader confirms an order as paid from the Telegram group",
)
async def confirm_order_from_group(
    order_uuid: UUID,
    payload: TraderBotConfirmRequest,
    order_service: OrderService = Depends(get_service(OrderService)),
) -> TraderBotConfirmResponse:
    order = await order_service.complete_order_from_trader_group(
        order_uuid=str(order_uuid),
        telegram_group_id=payload.telegram_group_id,
        actor_tg_id=payload.telegram_user_id,
    )
    return TraderBotConfirmResponse(order_uuid=str(order.uuid), status=order.status.value)


@router.post(
    "/orders/{order_uuid}/request-proof",
    response_model=TraderBotRequestProofResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_trader_bot_secret)],
    summary="Trader requests video/PDF proof from the merchant, from the Telegram group",
)
async def request_proof_from_group(
    order_uuid: UUID,
    payload: TraderBotRequestProofRequest,
    dispute_service: DisputeService = Depends(get_service(DisputeService)),
) -> TraderBotRequestProofResponse:
    dispute = await dispute_service.request_proof_from_trader_group(
        order_uuid=str(order_uuid),
        telegram_group_id=payload.telegram_group_id,
        kind=payload.kind,
    )
    return TraderBotRequestProofResponse(
        order_uuid=str(order_uuid),
        dispute_uuid=str(dispute.uuid),
        substatus=dispute.substatus.value if dispute.substatus else None,
    )


# ── Receipt anti-fraud check (trader taps «Проверить чек») ───────────────────

# Backend exception message → trader-facing Russian text. Anything not listed
# collapses to a neutral "service unavailable" so the trader never sees internals.
_BOT_RECEIPT_CHECK_ERRORS: dict[str, str] = {
    "Order has no receipt to verify": "По ордеру нет чека для проверки",
    "Insufficient WORK balance to pay for a receipt check":
        "Недостаточно средств на балансе для проверки чека",
    "Selected receipt-check provider is not available": "Выбранный провайдер недоступен",
}

_bot_receipt_check_log = get_logger(__name__)


class TraderBotProvidersRequest(BaseSchema):
    telegram_group_id: int


class TraderBotProviderItem(BaseSchema):
    id: int
    name: str
    price_usdt: float


class TraderBotReceiptCheckRequest(BaseSchema):
    telegram_group_id: int
    provider_id: int
    telegram_user_id: Optional[int] = None
    telegram_username: Optional[str] = None


class TraderBotReceiptCheckResponse(BaseSchema):
    status: str
    is_clean: Optional[bool] = None
    error_code: Optional[str] = None


@router.post(
    "/receipt-check/providers",
    response_model=List[TraderBotProviderItem],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_trader_bot_secret)],
    summary="List active receipt-check providers for the trader-bot picker",
)
async def list_receipt_check_providers_for_bot(
    payload: TraderBotProvidersRequest,
    check_service: ReceiptCheckService = Depends(get_service(ReceiptCheckService)),
) -> List[TraderBotProviderItem]:
    providers = await check_service.list_active_providers()
    return [
        TraderBotProviderItem(id=p.id, name=p.name, price_usdt=float(p.price_usdt or 0))
        for p in providers
    ]


@router.post(
    "/orders/{order_uuid}/receipt-check",
    response_model=TraderBotReceiptCheckResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(verify_trader_bot_secret)],
    summary="Run a manual receipt anti-fraud check (charges the trader) from the group",
)
async def run_receipt_check_from_group(
    order_uuid: UUID,
    payload: TraderBotReceiptCheckRequest,
    check_service: ReceiptCheckService = Depends(get_service(ReceiptCheckService)),
) -> TraderBotReceiptCheckResponse:
    try:
        check = await check_service.run_check_from_trader_group(
            order_uuid=str(order_uuid),
            telegram_group_id=payload.telegram_group_id,
            provider_id=payload.provider_id,
        )
    except ConflictException as exc:
        # Neutral message to the trader; keep the real cause for Sentry/admin logs
        # (mirrors the cabinet run_receipt_check).
        _bot_receipt_check_log.warning(
            "bot receipt-check conflict",
            order_uuid=str(order_uuid),
            telegram_group_id=payload.telegram_group_id,
            detail=exc.message,
        )
        raise ConflictException("Сервис проверки временно недоступен")
    except ValidationException as exc:
        msg = _BOT_RECEIPT_CHECK_ERRORS.get(exc.message)
        if msg is None:
            _bot_receipt_check_log.warning(
                "bot receipt-check validation hidden",
                order_uuid=str(order_uuid),
                telegram_group_id=payload.telegram_group_id,
                detail=exc.message,
            )
            raise ValidationException("Сервис проверки временно недоступен")
        raise ValidationException(msg)
    return TraderBotReceiptCheckResponse(
        status=check.status.value,
        is_clean=check.is_clean,
        error_code=check.error_code,
    )
