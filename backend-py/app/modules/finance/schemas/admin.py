from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from pydantic import Field

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency, WithdrawalStatus
from app.common.enums.users import UserRole
from app.modules.base.schemas import BaseResponseSchema, BaseSchema


class WithdrawalRequestBase(BaseSchema):
    amount: Decimal = Field(..., gt=0)
    currency: Currency = Currency.USDT
    destination_address: str = Field(..., min_length=10, max_length=255)


class WithdrawalRequestCreate(WithdrawalRequestBase):
    pass


class RejectWithdrawalRequest(BaseSchema):
    reason: str


class WithdrawalRequestResponse(BaseResponseSchema, WithdrawalRequestBase):
    id: int
    uuid: UUID
    user_role: UserRole
    user_id: int
    user_login: Optional[str] = None
    merchant_id: Optional[int] = None
    merchant_name: Optional[str] = None
    fee_amount: Decimal
    status: WithdrawalStatus
    created_at: datetime
    updated_at: datetime
    processed_at: Optional[datetime]
    processed_by_id: Optional[int]
    rejection_reason: Optional[str]


class BalanceRefInfo(BaseSchema):
    """Denormalised balance metadata attached to a ledger row.

    Lets the admin UI render "Из" / "В" columns as a badge that shows *who*
    the balance belongs to and which bucket it is (work / escrow / safe
    deposit / currency) without making a separate API call per row."""

    id: int
    type: BalanceType
    currency: Currency
    # "user" | "merchant" | "system"
    owner_kind: str
    owner_id: Optional[int] = None
    # Human-readable label — username for a user, merchant name for a merchant,
    # "Система" for the system / platform balance.
    owner_label: str


class LedgerEntryResponse(BaseResponseSchema):
    id: int
    from_balance_id: Optional[int] = None
    to_balance_id: Optional[int] = None
    # Enriched balance objects for the admin finances table. Kept alongside the
    # raw IDs above so existing clients that only read the IDs keep working.
    from_balance: Optional[BalanceRefInfo] = None
    to_balance: Optional[BalanceRefInfo] = None
    amount: Decimal
    currency: Currency
    reference_type: LedgerReferenceType
    reference_id: str
    description: Optional[str] = None
    created_at: datetime


# ── Top-up by TRC20 tx hash (Admin) ─────────────────────────────────────────


class AdminHashDepositRequest(BaseSchema):
    user_id: int = Field(..., description="Target trader user id")
    tx_hash: str = Field(..., min_length=1, max_length=100, description="TRC20 transaction hash")


class AdminHashDepositVerifyResponse(BaseSchema):
    user_id: int
    trader_login: Optional[str] = None
    tx_hash: str
    amount: float
    currency: str
    to_address: str
    from_address: str


class AdminHashDepositConfirmResponse(BaseSchema):
    ledger_entry_id: int
    user_id: int
    amount: float
    currency: str
    tx_hash: str
