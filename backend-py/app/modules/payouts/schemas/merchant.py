from datetime import datetime
from typing import Optional

from pydantic import Field

from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.modules.base.schemas import BaseResponseSchema, BaseSchema


class MerchantPayoutRequisites(BaseSchema):
    """Destination requisite (the END-USER's) the trader must pay to."""
    holder: str = Field(..., description="Account holder name")
    number: str = Field(..., description="Card / account number")
    extra: Optional[str] = Field(None, description="Bank or extra routing info")


class MerchantPayoutCreate(BaseSchema):
    amount: float = Field(..., gt=0, description="Amount to send to the end-user, in fiat")
    currency: Currency
    payment_method: PaymentMethod = Field(..., description="Destination method (card, sbp, sim)")
    payment_option: Optional[int] = Field(None, description="Specific bank/option id")
    external_id: str = Field(..., description="Merchant's payout id (idempotency key)")
    user_id: Optional[str] = Field(None, description="End-user id in the merchant's system")
    notification_url: Optional[str] = Field(None, description="Callback URL for this payout")
    ttl_minutes: Optional[int] = Field(
        None, ge=1, le=7 * 24 * 60,
        description="Lifetime in minutes; overrides the terminal default (clamped 1..7d)",
    )
    payment_requisites: MerchantPayoutRequisites


class MerchantPayoutResponse(BaseResponseSchema):
    """Merchant-facing view — no trader/internal ids, no destination echo."""
    id: str = Field(..., description="Payout UUID in PrimePay")
    external_id: str
    amount: float
    currency: Currency
    status: PayoutStatus
    created_at: datetime
    expires_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None


class MerchantPayoutListItem(BaseResponseSchema):
    """Cabinet (owner) view of a payout — the merchant sees their own terminals'
    payouts with the financials and the destination requisite they themselves
    provided, but never the trader's internal ids."""
    id: str = Field(..., description="Payout UUID in PrimePay")
    external_id: str
    payout_terminal_id: int
    terminal_name: Optional[str] = None
    payment_method: PaymentMethod
    amount: float
    currency: Currency
    amount_usdt: Optional[float] = None
    merchant_fee_usdt: Optional[float] = None
    status: PayoutStatus
    client_user_id: Optional[str] = None
    req_holder: Optional[str] = None
    req_number: Optional[str] = None
    req_extra: Optional[str] = None
    rejection_reason: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    canceled_at: Optional[datetime] = None


class PaginatedMerchantPayoutResponse(BaseResponseSchema):
    """Paginated envelope for the merchant cabinet payouts list — mirrors
    ``PaginatedOrderResponse``."""
    items: list[MerchantPayoutListItem]
    total: int
