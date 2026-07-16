"""Trader-facing долив schemas (requester + доливщик)."""
from datetime import datetime
from typing import Optional

from pydantic import Field

from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.modules.base.schemas import BaseResponseSchema, BaseSchema


class DolivCreateRequest(BaseSchema):
    requisite_id: int = Field(..., description="ID реквизита, оборот которого заполняет долив")
    amount: float = Field(..., gt=0, description="Сумма долива в фиате")


class DolivExecutorAccess(BaseSchema):
    """Whether the current trader is a доливщик (in the settings executor list) —
    drives whether the «Долив» tab is shown."""
    is_executor: bool


class DolivLimits(BaseSchema):
    """Trader-facing долив config — drives the create-modal hints («Доступный
    долив» range + «Стоимость» percent). ``max_amount`` of 0 means «no cap»."""
    min_amount: float
    max_amount: float
    price_percent: float


class DolivResponse(BaseResponseSchema):
    """One долив (a ``Payout`` row flagged is_doliv), shaped for the trader UI.

    ``id`` is the долив uuid; ``executor_reward_usdt`` is the доливщик's reward
    (stored on the payout's ``trader_fee_usdt``)."""
    id: str
    status: PayoutStatus
    amount: float
    currency: Currency
    amount_usdt: Optional[float] = None
    exchange_rate: Optional[float] = None
    price_usdt: Optional[float] = None
    executor_reward_usdt: Optional[float] = None
    payment_method: PaymentMethod
    req_holder: Optional[str] = None
    req_number: Optional[str] = None
    req_extra: Optional[str] = None
    logo_url: Optional[str] = None  # bank logo (from the requisite's payment option)
    payment_option_name: Optional[str] = None  # bank / payment-option name (Банк column)
    has_receipt: bool = False  # доливщик attached a check (downloadable + sent to bot)
    refill_order_id: Optional[int] = None
    refill_requisite_id: Optional[int] = None
    requester_trader_id: Optional[int] = None  # whose requisite is filled (admin view)
    executor_trader_id: Optional[int] = None   # доливщик who executed it (admin view)
    created_at: datetime
    claimed_at: Optional[datetime] = None
    claim_expires_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None

    @classmethod
    def from_payout(cls, p, *, logo_url=None, payment_option_name=None) -> "DolivResponse":
        def _f(v):
            return float(v) if v is not None else None

        return cls(
            id=str(p.uuid),
            status=p.status,
            amount=float(p.amount),
            currency=p.currency,
            amount_usdt=_f(p.amount_usdt),
            exchange_rate=_f(p.exchange_rate),
            price_usdt=_f(p.doliv_price_usdt),
            executor_reward_usdt=_f(p.trader_fee_usdt),
            payment_method=p.payment_method,
            req_holder=p.req_holder,
            req_number=p.req_number,
            req_extra=p.req_extra,
            logo_url=logo_url,
            payment_option_name=payment_option_name,
            has_receipt=bool(p.receipt_file),
            refill_order_id=p.refill_order_id,
            refill_requisite_id=p.refill_requisite_id,
            requester_trader_id=p.requester_trader_id,
            executor_trader_id=p.trader_id,
            created_at=p.created_at,
            claimed_at=p.claimed_at,
            claim_expires_at=p.claim_expires_at,
            expires_at=p.expires_at,
            completed_at=p.completed_at,
            canceled_at=p.canceled_at,
        )
