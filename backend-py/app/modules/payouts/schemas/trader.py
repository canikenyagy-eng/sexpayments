from datetime import datetime
from typing import Optional

from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.modules.base.schemas import BaseResponseSchema


class TraderPayoutPoolItem(BaseResponseSchema):
    """Pool view BEFORE claim — destination requisite is intentionally hidden."""
    id: str
    amount: float
    currency: Currency
    payment_method: PaymentMethod
    amount_usdt: Optional[float] = None
    payment_option_name: Optional[str] = None
    created_at: datetime
    expires_at: Optional[datetime] = None


class TraderPayoutResponse(BaseResponseSchema):
    """Full view AFTER claim (or for the trader's own payouts) — requisite visible."""
    id: str
    status: PayoutStatus
    amount: float
    currency: Currency
    payment_method: PaymentMethod
    payment_option_name: Optional[str] = None
    amount_usdt: Optional[float] = None
    trader_fee_usdt: Optional[float] = None
    # Destination requisite (visible once claimed)
    req_holder: Optional[str] = None
    req_number: Optional[str] = None
    req_extra: Optional[str] = None
    client_user_id: Optional[str] = None
    receipt_file: Optional[str] = None
    receipt_uploaded_at: Optional[datetime] = None
    claimed_at: Optional[datetime] = None
    claim_expires_at: Optional[datetime] = None
    trader_hold_until: Optional[datetime] = None
    created_at: datetime
    expires_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
