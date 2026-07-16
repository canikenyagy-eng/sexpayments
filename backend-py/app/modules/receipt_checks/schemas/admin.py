from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional

from pydantic import Field, HttpUrl, field_validator

from app.common.enums.receipt_checks import (
    ReceiptCheckProviderAdapter,
    ReceiptCheckStatus,
    ReceiptCheckTrigger,
)
from app.modules.base.schemas import BaseResponseSchema, BaseSchema


# ── Provider (admin) ──────────────────────────────────────────────────────


class ProviderBase(BaseSchema):
    code: str = Field(..., min_length=2, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    name: str = Field(..., min_length=1, max_length=255)
    adapter_type: ReceiptCheckProviderAdapter
    base_url: str = Field(..., min_length=1, max_length=512)
    price_usdt: Decimal = Field(..., ge=0, le=Decimal("10000"))
    request_timeout_ms: int = Field(90000, ge=1000, le=300000)
    settings: dict = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v.rstrip("/")


class ProviderCreate(ProviderBase):
    api_key: str = Field(..., min_length=1, max_length=512, description="Plaintext API key — stored encrypted, read-once")
    is_active: bool = False


class ProviderUpdate(BaseSchema):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    base_url: Optional[str] = Field(None, max_length=512)
    api_key: Optional[str] = Field(None, min_length=1, max_length=512, description="Replace API key (read-once); omit to keep existing")
    price_usdt: Optional[Decimal] = Field(None, ge=0, le=Decimal("10000"))
    request_timeout_ms: Optional[int] = Field(None, ge=1000, le=300000)
    is_active: Optional[bool] = None
    settings: Optional[dict] = None

    @field_validator("base_url")
    @classmethod
    def _validate_url(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not v.startswith(("http://", "https://")):
            raise ValueError("base_url must start with http:// or https://")
        return v.rstrip("/")


class ProviderResponse(BaseResponseSchema):
    id: int
    code: str
    name: str
    adapter_type: ReceiptCheckProviderAdapter
    is_active: bool
    base_url: str
    api_key_masked: Optional[str] = Field(None, description="`sk_live_***abcd` style mask. Plaintext is never returned.")
    price_usdt: Decimal
    request_timeout_ms: int
    settings: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class ProviderBalanceResponse(BaseResponseSchema):
    """Remote balance pulled live from the provider — never persisted.

    `remaining` is what the trader-facing UI uses; per-bucket fields are
    informational. `error` is non-null when the call to the provider fails
    so the admin UI can render a friendly message instead of a blank card.
    """

    remaining: Optional[int] = None
    own: Optional[int] = None
    gifted: Optional[int] = None
    total_checks: Optional[int] = None
    error: Optional[str] = None
    fetched_at: datetime


# ── Receipt check (trader / shared) ───────────────────────────────────────


class ReceiptCheckVerdictItem(BaseSchema):
    type: str
    message: Optional[str] = None


class ReceiptCheckResponse(BaseResponseSchema):
    id: int
    order_id: int
    provider_id: Optional[int] = None
    trader_user_id: Optional[int] = None
    trigger: ReceiptCheckTrigger
    status: ReceiptCheckStatus
    is_clean: Optional[bool] = None
    verdict: Optional[List[ReceiptCheckVerdictItem]] = None
    parsed_data: Optional[dict] = None
    provider_check_id: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    price_usdt: Decimal
    charged: bool
    refunded: bool
    created_at: datetime
    finished_at: Optional[datetime] = None

    @classmethod
    def from_orm_check(cls, check) -> "ReceiptCheckResponse":
        """Build the API response from a `ReceiptCheck` ORM row. Kept here
        (next to the schema definition) so handlers don't have to know which
        ORM fields exist and stay free of serialization logic."""
        return cls(
            id=check.id,
            order_id=check.order_id,
            provider_id=check.provider_id,
            trader_user_id=check.trader_user_id,
            trigger=check.trigger,
            status=check.status,
            is_clean=check.is_clean,
            verdict=check.verdict,
            parsed_data=check.parsed_data,
            provider_check_id=check.provider_check_id,
            error_code=check.error_code,
            error_message=check.error_message,
            price_usdt=check.price_usdt,
            charged=check.charged,
            refunded=check.refunded,
            created_at=check.created_at,
            finished_at=check.finished_at,
        )


class ManualCheckRequest(BaseSchema):
    """Trader-initiated manual check. The order/file is read server-side
    from `orders.receipt_file`; the trader only picks which provider to use."""

    confirm: bool = Field(True, description="Confirmation flag from the modal (kept explicit so accidental clicks don't charge)")
    provider_id: Optional[int] = Field(
        None,
        description="ID выбранного активного провайдера; null — использовать дефолт трейдера/первый активный",
    )


class BulkLatestRequest(BaseSchema):
    """List of order UUIDs we want the most recent receipt-check for."""

    order_uuids: List[str] = Field(..., min_length=1, max_length=500)


class ReceiptAutoCheckToggle(BaseSchema):
    enabled: bool


# ── Adapter-internal DTO (not exposed) ────────────────────────────────────


class ProviderCheckResult(BaseSchema):
    """Result of a single low-level adapter call. Service layer translates
    this into ReceiptCheck rows + ledger transfers."""

    is_clean: Optional[bool] = None
    verdict: List[ReceiptCheckVerdictItem] = Field(default_factory=list)
    parsed_data: dict = Field(default_factory=dict)
    provider_check_id: Optional[str] = None
    provider_tx_id: Optional[str] = None
    raw_response: Any = None
    # When True the provider explicitly told us the call wasn't billable on
    # their side (5xx / quota_exhausted / etc.) — we refund the trader's
    # USDT charge before responding.
    refundable: bool = False
    error_code: Optional[str] = None
    error_message: Optional[str] = None
