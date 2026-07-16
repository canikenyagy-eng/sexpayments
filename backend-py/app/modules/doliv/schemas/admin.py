"""Admin-facing долив platform settings + the admin «Доливы» page schemas."""
from datetime import datetime
from typing import List, Optional

from pydantic import Field

from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.modules.base.schemas import BaseResponseSchema, BaseSchema


class AdminDolivResponse(BaseResponseSchema):
    """One долив for the admin «Доливы» page — full detail incl. resolved trader
    usernames (the trader-facing schema exposes only ids)."""

    id: str  # долив uuid
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
    payment_option_name: Optional[str] = None  # bank / payment-option name
    has_receipt: bool = False  # доливщик attached a check
    refill_order_id: Optional[int] = None
    refill_requisite_id: Optional[int] = None
    requester_trader_id: Optional[int] = None
    requester_username: Optional[str] = None
    executor_trader_id: Optional[int] = None
    executor_username: Optional[str] = None
    created_at: datetime
    claimed_at: Optional[datetime] = None
    claim_expires_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None

    @classmethod
    def from_payout(cls, p, *, requester_username=None, executor_username=None, logo_url=None,
                    payment_option_name=None) -> "AdminDolivResponse":
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
            requester_username=requester_username,
            executor_trader_id=p.trader_id,
            executor_username=executor_username,
            created_at=p.created_at,
            claimed_at=p.claimed_at,
            claim_expires_at=p.claim_expires_at,
            expires_at=p.expires_at,
            completed_at=p.completed_at,
            canceled_at=p.canceled_at,
        )


class AdminDolivListResponse(BaseResponseSchema):
    """Paginated admin доливы list."""

    items: List[AdminDolivResponse]
    total: int


class AdminDolivStatusUpdate(BaseSchema):
    """Admin free status override — the долив state machine applies the money."""

    status: PayoutStatus


class AdminDolivSettings(BaseResponseSchema):
    """The долив knobs on the platform-settings «Долив» tab."""

    min_amount: float
    max_amount: float
    price_percent: float
    executor_reward_percent: float
    executor_user_ids: str  # CSV of trader user ids


class AdminDolivSettingsUpdate(BaseSchema):
    """Partial update — any field may be omitted."""

    min_amount: Optional[float] = Field(None, ge=0)
    max_amount: Optional[float] = Field(None, ge=0)
    price_percent: Optional[float] = Field(None, ge=0, le=100)
    executor_reward_percent: Optional[float] = Field(None, ge=0, le=100)
    executor_user_ids: Optional[str] = None
