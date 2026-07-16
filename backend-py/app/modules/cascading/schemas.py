from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional

from pydantic import Field, computed_field

from app.common.enums.payments import PaymentMethod
from app.common.enums.cascading import (
    CascadeAttemptStatus,
    CascadeMode,
    CascadeRateSource,
)
from app.modules.base.schemas import BaseResponseSchema, BaseSchema


# ---------- providers ----------

class CascadeProviderBase(BaseSchema):
    code: str
    name: str
    adapter_type: str
    is_active: bool = True
    base_url: str
    fees: Dict[str, float] = Field(
        default_factory=dict,
        description="Per-method fee in % — keys are PaymentMethod values (sbp/card/sim).",
    )
    rate_source: CascadeRateSource = CascadeRateSource.PROVIDER
    rate_config_id: Optional[int] = Field(
        None,
        description="Required when rate_source = 'platform'. References rate_configs.id.",
    )
    min_amount_fiat: Optional[float] = None
    max_amount_fiat: Optional[float] = None
    cb_window_seconds: int = 300
    cb_threshold_failures: int = 5
    cb_threshold_rate: float = 0.5
    cb_cooldown_seconds: int = 600
    request_timeout_ms: int = 3000
    cancel_timeout_ms: int = 2000
    priority_weight: int = 100
    settings: Dict[str, Any] = Field(
        default_factory=dict,
        description="Adapter-specific settings; shape defined by adapter SETTINGS_SCHEMA.",
    )


class CascadeProviderCreate(CascadeProviderBase):
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    webhook_secret: Optional[str] = None
    initial_balance_usdt: float = 0.0  # seed virtual user's WORK balance


class CascadeProviderUpdate(BaseSchema):
    name: Optional[str] = None
    adapter_type: Optional[str] = None
    is_active: Optional[bool] = None
    base_url: Optional[str] = None
    fees: Optional[Dict[str, float]] = None
    rate_source: Optional[CascadeRateSource] = None
    rate_config_id: Optional[int] = None
    min_amount_fiat: Optional[float] = None
    max_amount_fiat: Optional[float] = None
    cb_window_seconds: Optional[int] = None
    cb_threshold_failures: Optional[int] = None
    cb_threshold_rate: Optional[float] = None
    cb_cooldown_seconds: Optional[int] = None
    request_timeout_ms: Optional[int] = None
    cancel_timeout_ms: Optional[int] = None
    priority_weight: Optional[int] = None
    settings: Optional[Dict[str, Any]] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    webhook_secret: Optional[str] = None


class CascadeProviderResponse(BaseResponseSchema):
    id: int
    code: str
    name: str
    adapter_type: str
    is_active: bool
    base_url: str
    fees: Dict[str, Any]
    rate_source: CascadeRateSource
    rate_config_id: Optional[int] = None
    min_amount_fiat: Optional[Decimal] = None
    max_amount_fiat: Optional[Decimal] = None
    cb_window_seconds: int
    cb_threshold_failures: int
    cb_threshold_rate: float
    cb_cooldown_seconds: int
    disabled_until: Optional[datetime] = None
    request_timeout_ms: int
    cancel_timeout_ms: int
    priority_weight: int
    settings: Dict[str, Any]
    virtual_user_id: int
    virtual_trader_id: int
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def callback_url(self) -> str:
        """Auto-formed webhook URL for *this* provider — what to register on
        the upstream side. Empty when ``PROJECT_BASE_URL`` isn't set.
        """
        from app.modules.cascading.integrations.base import ProviderAdapter

        return ProviderAdapter.build_cascade_callback_url(self.code)


# ---------- adapter catalog ----------


class CascadeAdapterOption(BaseSchema):
    value: str
    label: str


class CascadeAdapterFieldSpec(BaseSchema):
    key: str
    label: str
    type: str
    required: bool = False
    default: Any = None
    description: str = ""
    placeholder: str = ""
    secret: bool = False
    options: List[CascadeAdapterOption] = Field(default_factory=list)
    value_type: str = "string"
    min: Optional[float] = None
    max: Optional[float] = None


class CascadeAdapterInfoResponse(BaseSchema):
    code: str
    display_name: str
    description: str
    supports_provider_rate: bool
    settings_schema: List[CascadeAdapterFieldSpec] = Field(default_factory=list)
    supported_methods: List[str] = Field(default_factory=list)
    # Per-adapter credentials form (powers the "Аутентификация" tab in
    # the admin UI). Same field-spec shape as ``settings_schema``;
    # ``key`` is one of ``api_key`` / ``api_secret`` / ``webhook_secret``.
    credentials_schema: List[CascadeAdapterFieldSpec] = Field(default_factory=list)
    # Filename of an optional adapter logo in ``frontend-vue/public/logos/``.
    # Empty string when the adapter ships without a logo — the admin UI
    # renders just the text label in that case.
    logo_filename: str = ""


class CascadeProviderBalanceAdjust(BaseSchema):
    delta_usdt: float
    reason: str = "manual_adjustment"


class CascadeProviderTestIssueRequest(BaseSchema):
    """Admin-side "try this provider live" request.

    Drives ``POST /api/v1/cascade/providers/{id}/test-issue``. The
    endpoint reserves a real requisite on the provider side so admins
    can verify auth/signing/parsing end-to-end without involving the
    cascade scheduler — and immediately rolls the reservation back
    unless ``auto_cancel=false`` is passed explicitly.
    """

    amount: float = Field(..., gt=0, description="Fiat amount in provider currency")
    payment_method: PaymentMethod = Field(
        ..., description="Our internal PaymentMethod (sbp / card / sim / ...)"
    )
    payment_option_code: Optional[str] = Field(
        None,
        description=(
            "Our PaymentOption.code (e.g. 'sber'). Optional — when omitted "
            "the provider picks any matching bank."
        ),
    )
    auto_cancel: bool = Field(
        True,
        description=(
            "Cancel the reservation immediately after a successful issue. "
            "Set to false if you want to keep the requisite for further "
            "manual tests (then cancel via the cascade CLI / API)."
        ),
    )


# ---------- groups ----------

class CascadeGroupBase(BaseSchema):
    name: str
    description: Optional[str] = None
    tier: int = 1
    timeout_ms: int = 3000
    is_active: bool = True


class CascadeGroupCreate(CascadeGroupBase):
    provider_ids: List[int] = Field(default_factory=list)
    merchant_ids: List[int] = Field(default_factory=list)


class CascadeGroupUpdate(BaseSchema):
    name: Optional[str] = None
    description: Optional[str] = None
    tier: Optional[int] = None
    timeout_ms: Optional[int] = None
    is_active: Optional[bool] = None


class CascadeGroupProviderRef(BaseResponseSchema):
    id: int
    code: str
    name: str
    is_active: bool


class CascadeGroupMerchantRef(BaseResponseSchema):
    id: int
    name: Optional[str] = None


class CascadeGroupResponse(BaseResponseSchema):
    id: int
    name: str
    description: Optional[str] = None
    tier: int
    timeout_ms: int
    is_active: bool
    created_at: datetime
    updated_at: datetime
    providers: List[CascadeGroupProviderRef] = Field(default_factory=list)
    merchants: List[CascadeGroupMerchantRef] = Field(default_factory=list)


# ---------- attempts ----------

class CascadeOrderAttemptResponse(BaseResponseSchema):
    id: int
    order_id: int
    provider_id: int
    group_id: Optional[int] = None
    tier: Optional[int] = None
    started_at: datetime
    finished_at: Optional[datetime] = None
    latency_ms: Optional[int] = None
    status: CascadeAttemptStatus
    refusal_reason: Optional[str] = None
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    external_order_id: Optional[str] = None
    requisite_snapshot: Optional[Dict[str, Any]] = None
    requisite_id: Optional[int] = None
    provider_rate: Optional[Decimal] = None
    provider_fee_usdt: Optional[Decimal] = None
    our_profit_usdt: Optional[Decimal] = None
    idempotency_key: str
    created_at: datetime


# ---------- inbound provider callbacks (admin debug) ----------

class ProviderCallbackAttemptResponse(BaseResponseSchema):
    """Persisted inbound callback record — surfaced on the admin Callbacks
    page ``Провайдеры`` tab. Every POST to ``/api/cascade/v1/callbacks/{code}``
    produces exactly one row, success or failure."""
    id: int
    provider_id: Optional[int] = None
    provider_code: str
    order_id: Optional[int] = None
    external_order_id: Optional[str] = None
    request_headers: Optional[Dict[str, Any]] = None
    request_body: Optional[str] = None
    signature_valid: bool
    parsed_status: Optional[str] = None
    response_status: Optional[int] = None
    response_body: Optional[str] = None
    error_message: Optional[str] = None
    created_at: datetime


# ---------- metrics ----------

class CascadeMetricPoint(BaseResponseSchema):
    bucket_at: datetime
    request_count: int
    success_count: int
    failure_count: int
    timeout_count: int
    cancel_count: int
    avg_latency_ms: float
    success_rate: float
    total_volume_usdt: Decimal
    total_profit_usdt: Decimal


class CascadeProviderMetricsResponse(BaseResponseSchema):
    provider_id: int
    points: List[CascadeMetricPoint]
    totals: CascadeMetricPoint


# ---------- merchant cascade settings (sub-shape, lives next to MerchantUpdate) ----------

class MerchantCascadeUpdate(BaseSchema):
    cascade_mode: CascadeMode
    group_ids: Optional[List[int]] = None


# ---------- provider request log (ClickHouse-backed; admin "Запросы провайдеров") ----------

class ProviderRequestLogItem(BaseSchema):
    """One outbound provider HTTP request as stored in ClickHouse."""
    ts: datetime
    request_id: str = ""
    provider_id: str = ""
    provider_code: str = ""
    order_id: str = ""
    method: str = ""
    url: str = ""
    request_headers: str = ""   # JSON (credential headers masked)
    request_body: str = ""
    response_status: int = 0     # 0 on network error / timeout
    response_body: str = ""
    success: bool = False
    error: str = ""
    provider_latency_ms: int = 0
    e2e_ms: int = 0              # merchant request → response (0 if background)
    request_type: str = "other"  # payin / cancel / balance / check / upload / …


class ProviderCallbackLogItem(BaseSchema):
    """One inbound provider callback as stored in ClickHouse. Shaped to match
    what the admin Callbacks page already consumes (``created_at`` /
    ``error_message`` / ``request_headers`` as an object)."""
    provider_id: Optional[int] = None
    provider_code: str = ""
    order_id: Optional[int] = None
    external_order_id: Optional[str] = None
    signature_valid: bool = False
    parsed_status: Optional[str] = None
    request_headers: Optional[Dict[str, Any]] = None
    request_body: Optional[str] = None
    response_status: Optional[int] = None
    response_body: Optional[str] = None
    error_message: Optional[str] = None
    processing_ms: int = 0
    request_id: Optional[str] = None
    created_at: datetime
