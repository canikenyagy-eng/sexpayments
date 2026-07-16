from datetime import datetime
from typing import List, Optional

from pydantic import Field

from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.modules.base.schemas import BaseResponseSchema, BaseSchema


class AdminPayoutResponse(BaseResponseSchema):
    """Full admin view — everything, including internal ids and fees."""
    id: str
    external_id: str
    payout_terminal_id: Optional[int] = None
    trader_id: Optional[int] = None
    client_user_id: Optional[str] = None
    payment_method: PaymentMethod
    payment_option_name: Optional[str] = None
    amount: float
    currency: Currency
    amount_usdt: Optional[float] = None
    exchange_rate: Optional[float] = None
    merchant_fee_usdt: Optional[float] = None
    trader_fee_usdt: Optional[float] = None
    req_holder: Optional[str] = None
    req_number: Optional[str] = None
    req_extra: Optional[str] = None
    status: PayoutStatus
    rejection_reason: Optional[str] = None
    receipt_file: Optional[str] = None
    receipt_uploaded_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    claim_expires_at: Optional[datetime] = None
    trader_hold_until: Optional[datetime] = None
    hold_released_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    created_at: datetime
    completed_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None


class AdminPayoutListResponse(BaseResponseSchema):
    """Paginated envelope for the admin payouts list (mirrors the orders list)."""
    items: List[AdminPayoutResponse]
    total: int


class AdminPayoutRejectRequest(BaseSchema):
    reason: str = Field(..., max_length=500)


# ── config schemas ──────────────────────────────────────────────────────────

class AdminPayoutTerminalConfig(BaseSchema):
    """Per-terminal payout config update (admin). All fields optional/patch."""
    name: Optional[str] = None
    status: Optional[str] = None
    currency: Optional[Currency] = None
    rate_config_id: Optional[int] = None
    commission_percent: Optional[float] = Field(None, ge=0)
    ttl_minutes: Optional[int] = Field(None, ge=1)
    receipts_to_close: Optional[int] = Field(None, ge=1)
    min_amount: Optional[float] = Field(None, ge=0)
    max_amount: Optional[float] = Field(None, ge=0)
    webhook_url: Optional[str] = None
    trader_ids: Optional[List[int]] = None  # replace the trader ACL


class AdminPayoutTerminalCreate(BaseSchema):
    owner_user_id: int
    name: str
    status: Optional[str] = None
    currency: Optional[Currency] = None
    rate_config_id: Optional[int] = None
    commission_percent: Optional[float] = Field(0, ge=0)
    ttl_minutes: Optional[int] = Field(60, ge=1)
    receipts_to_close: Optional[int] = Field(1, ge=1)
    min_amount: Optional[float] = Field(None, ge=0)
    max_amount: Optional[float] = Field(None, ge=0)
    webhook_url: Optional[str] = None
    trader_ids: Optional[List[int]] = None


class AdminPayoutTerminalResponse(BaseResponseSchema):
    id: int
    owner_user_id: int
    name: str
    status: str
    currency: Currency
    api_key: str
    rate_config_id: Optional[int] = None
    commission_percent: float
    ttl_minutes: int
    receipts_to_close: int
    min_amount: Optional[float] = None
    max_amount: Optional[float] = None
    webhook_url: Optional[str] = None
    work_usdt: float = 0
    escrow_usdt: float = 0
    trader_ids: List[int] = []
    created_at: datetime


class AdminPayoutTerminalCreated(AdminPayoutTerminalResponse):
    api_secret: str  # shown ONCE at creation


class AdminPayoutTopupRequest(BaseSchema):
    amount: float = Field(..., gt=0)


class AdminPayoutReceiptItem(BaseResponseSchema):
    id: int
    payout_id: int
    trader_id: int
    amount: float
    file: Optional[str] = None
    status: str
    rejection_reason: Optional[str] = None
    created_at: datetime
    moderated_at: Optional[datetime] = None


class AdminTraderPayoutConfig(BaseSchema):
    is_payout_active: Optional[bool] = None
    payout_fee_percent: Optional[float] = Field(None, ge=0)
    payout_hold_hours: Optional[int] = Field(None, ge=0)
    # None → fall back to the global default
    payout_receipt_auto: Optional[bool] = None


class GlobalPayoutConfig(BaseSchema):
    payout_claim_ttl_seconds: Optional[int] = Field(None, ge=60)
    payout_receipt_auto_default: Optional[bool] = None
