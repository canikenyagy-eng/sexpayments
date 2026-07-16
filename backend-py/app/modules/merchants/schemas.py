from typing import Any, Dict, List, Optional, Union
from pydantic import AliasChoices, Field, field_validator, model_validator

from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.payments import PaymentMethod
from app.modules.base.schemas import BaseResponseSchema, BaseSchema
from app.modules.payments.schemas import PaymentOptionResponse


class PaymentMethodInfo(BaseSchema):
    method: PaymentMethod
    fee_percentage: float = Field(0.0, description="Fee percentage for this specific method")


class ApiKeyResetResponse(BaseSchema):
    api_key: str
    api_secret: str


class TelegramUser(BaseSchema):
    """Telegram user with an optional human-readable label.

    `active` marks the currently selected bot terminal for this TG user
    and is controlled by bot state endpoints (not by the UI).
    """
    id: int
    label: Optional[str] = None
    active: bool = False

    @model_validator(mode="before")
    @classmethod
    def _accept_plain_int(cls, value: Any) -> Any:
        if isinstance(value, int) and not isinstance(value, bool):
            return {"id": value, "label": None, "active": False}
        return value


# Accept either plain int or {id, label}; UI sends list of objects going forward.
TelegramUserInput = Union[TelegramUser, int]


class MerchantTraderBrief(BaseResponseSchema):
    id: int
    user_id: int


class MerchantTraderGroupBrief(BaseResponseSchema):
    id: int
    name: str


class MerchantAdminResponse(BaseResponseSchema):
    """Merchant data for admin list/detail view (no secrets)."""
    id: int
    user_id: int
    name: Optional[str] = None
    status: TerminalStatus
    currency: Currency
    webhook_url: Optional[str] = None
    order_ttl_seconds: int = 1800
    requisite_search_timeout_ms: int = 300
    fees: Dict[str, Any] = Field(default_factory=dict)
    withdrawal_fee_fixed: float = 0.0
    rate_config_id: Optional[int] = None
    telegram_user_ids: List[TelegramUser] = Field(default_factory=list)
    traders: List[MerchantTraderBrief] = Field(default_factory=list)
    trader_groups: List[MerchantTraderGroupBrief] = Field(default_factory=list)
    cascade_mode: str = "off"
    cascade_group_ids: List[int] = Field(
        default_factory=list,
        validation_alias=AliasChoices("cascade_group_ids", "cascade_groups"),
    )
    receipt_premoderation_enabled: Optional[bool] = None
    notify_telegram_group_id: Optional[int] = None
    dispute_telegram_group_id: Optional[int] = None
    dispute_id_mask: Optional[str] = None
    unique_clients_enabled: bool = False
    proof_request_notify_enabled: bool = True

    @field_validator("cascade_mode", mode="before")
    @classmethod
    def _enum_to_value(cls, v: Any) -> Any:
        if v is None:
            return "off"
        return v.value if hasattr(v, "value") else str(v)

    @field_validator("cascade_group_ids", mode="before")
    @classmethod
    def _resolve_cascade_groups(cls, v: Any) -> Any:
        if v is None:
            return []
        if isinstance(v, list) and v and not isinstance(v[0], int):
            return sorted([item.id for item in v if hasattr(item, "id")])
        return v


class MerchantProfileResponse(BaseSchema):
    id: int
    status: TerminalStatus
    currency: Currency
    webhook_url: Optional[str]
    balance: float = Field(0.0, description="Current WORK balance")
    payment_methods: List[PaymentMethodInfo] = Field(default_factory=list)


class MerchantFullProfileResponse(BaseSchema):
    """Extended profile returned via JWT for the merchant dashboard."""
    id: int
    name: Optional[str] = None
    status: TerminalStatus
    currency: Currency
    webhook_url: Optional[str]
    api_key_masked: str = Field(..., description="Masked API key (first 8 + last 4 chars)")
    balance_work: float = 0.0
    balance_escrow: float = 0.0
    order_ttl_seconds: int = 1800
    requisite_search_timeout_ms: int = 300
    fees: Dict[str, Any] = Field(default_factory=dict)
    withdrawal_fee_fixed: float = 0.0
    payment_methods: List[PaymentMethodInfo] = Field(default_factory=list)
    telegram_user_ids: List[TelegramUser] = Field(default_factory=list)


class MerchantSettingsUpdate(BaseSchema):
    webhook_url: Optional[str] = None
    order_ttl_seconds: Optional[int] = Field(None, ge=1, le=7200)
    requisite_search_timeout_ms: Optional[int] = Field(None, ge=1, le=60000)
    name: Optional[str] = Field(None, max_length=255)
    telegram_user_ids: Optional[List[TelegramUserInput]] = Field(
        None,
        description=(
            "Telegram users allowed to use this merchant via the bot. "
            "Accepts either plain IDs (int) or objects {id, label}."
        ),
    )


class MerchantCreateRequest(BaseSchema):
    name: Optional[str] = Field(None, max_length=255)


class MerchantCreateResponse(BaseSchema):
    id: int
    name: Optional[str]
    status: TerminalStatus
    currency: Currency
    api_key: str
    api_secret: str


class MerchantListItem(BaseResponseSchema):
    id: int
    name: Optional[str] = None
    status: TerminalStatus
    currency: Currency
    api_key_masked: str
    webhook_url: Optional[str] = None
    order_ttl_seconds: int = 1800


class MerchantAdminUpdate(BaseSchema):
    name: Optional[str] = Field(None, max_length=255)
    status: Optional[TerminalStatus] = None
    webhook_url: Optional[str] = None
    order_ttl_seconds: Optional[int] = Field(None, ge=1, le=7200)
    requisite_search_timeout_ms: Optional[int] = Field(None, ge=1, le=60000)
    fees: Optional[Dict[str, Any]] = None
    withdrawal_fee_fixed: Optional[float] = Field(None, ge=0)
    rate_config_id: Optional[int] = None
    telegram_user_ids: Optional[List[TelegramUserInput]] = Field(
        None,
        description=(
            "Telegram users allowed to use this merchant via the bot. "
            "Accepts either plain IDs (int) or objects {id, label}."
        ),
    )
    trader_ids: Optional[List[int]] = Field(
        None,
        description="Traders directly bound to this merchant (full replacement).",
    )
    group_ids: Optional[List[int]] = Field(
        None,
        description="Trader groups bound to this merchant (full replacement).",
    )
    cascade_mode: Optional[str] = Field(
        None,
        description="Cascade routing mode: off / grouped / pooled",
    )
    cascade_group_ids: Optional[List[int]] = Field(
        None,
        description="Cascade groups bound to this merchant (full replacement).",
    )
    receipt_premoderation_enabled: Optional[bool] = Field(
        None,
        description=(
            "Per-merchant override for receipt premoderation. NULL — fall "
            "back to the global PlatformSetting flag; True/False — force "
            "the toggle for this merchant only."
        ),
    )
    notify_telegram_group_id: Optional[int] = Field(
        None,
        description=(
            "Telegram chat_id of the group where merchant-notify-bot will "
            "post premoderation rejections (PDF/Video re-upload requests). "
            "NULL — no bot push (the rejection is still visible via the "
            "order's moderation_status)."
        ),
    )
    dispute_telegram_group_id: Optional[int] = Field(
        None,
        description=(
            "Telegram chat_id of the group/channel where merchant-dispute-bot "
            "reads inbound receipts (order uuid/external_id + file) from the "
            "merchant or their staff and feeds them into premoderation. "
            "NULL — no dispute-intake chat bound."
        ),
    )
    dispute_id_mask: Optional[str] = Field(
        None,
        max_length=50,
        description=(
            "Token-position mask to extract OUR order id from a free-form appeal "
            "message. Grammar: 'N' or 'word:N' = the N-th whitespace token "
            "(1-based); 'uuid:N' = the N-th UUID-shaped token. The picked token "
            "is resolved against our DB (uuid → external_id); NULL — no mask "
            "(scan every token)."
        ),
    )
    unique_clients_enabled: Optional[bool] = Field(
        None,
        description=(
            "When ON, the admin order modal shows a «Client» block (our internal "
            "public_id + all-time deals/conversion) for this merchant's orders. "
            "OFF (default) — not rendered and not sent over the API."
        ),
    )
    proof_request_notify_enabled: Optional[bool] = Field(
        None,
        description=(
            "When ON (default), a PDF/video proof request is pushed to the "
            "merchant's chat (merchant-notify-bot, or merchant-bot with an attach "
            "button for bot-created orders). OFF — only the API webhook carries it."
        ),
    )


class BotMerchantItem(BaseSchema):
    id: int
    name: Optional[str] = None
    status: str
    currency: str


class BotTerminalBalance(BaseSchema):
    id: int
    name: Optional[str] = None
    currency: str
    work: float
    escrow: float


class BotBalancesResponse(BaseSchema):
    currency: str = "USDT"
    total_work: float
    total_escrow: float
    owner_work: float
    owner_escrow: float
    terminals: List[BotTerminalBalance] = Field(default_factory=list)


class BotMethodLimits(BaseSchema):
    """Aggregated payin capacity per payment method for a single terminal.

    The single ``available`` field answers the merchant's actual question:
    "сколько RUB я могу принять прямо сейчас через этот метод". It's the
    sum across eligible requisites of ``MIN(daily_room, monthly_room)`` —
    the binding constraint differs per requisite (one might be limited
    by today's turnover, another by the monthly cap), so we collapse to
    "the number you can actually push through right now" per requisite
    and add them up.
    """
    payment_method: str
    currency: str
    available: float = Field(
        ...,
        description=(
            "Fiat amount that can be accepted through the pool RIGHT NOW: "
            "Σ over eligible requisites of MIN(daily_remaining, monthly_remaining), "
            "with in-flight pending order amounts already subtracted from each."
        ),
    )
    min_amount: float = Field(
        ..., description="Smallest single-order amount accepted by ANY eligible requisite of this method (per-transaction config floor)."
    )
    max_amount: float = Field(
        ...,
        description=(
            "Largest single order that will actually pass RIGHT NOW: per "
            "requisite the cap is MIN(limit_max_transaction, daily_room, "
            "monthly_room); the pool reports MAX of those caps. May drop "
            "below ``limit_max_transaction`` if all requisites with high "
            "per-transaction caps have their daily/monthly headroom eaten."
        ),
    )
    concurrent_slots: Optional[int] = Field(
        None,
        description="Free concurrent-order slots Σ(limit_max_concurrent_orders − active count); null when at least one eligible requisite has no limit (unlimited).",
    )
    requisites_count: int = Field(
        ...,
        description=(
            "Number of currently active and eligible requisites contributing to "
            "this pool: status=ENABLED, is_active=true, is_archived=false, "
            "trader status=ENABLED, trader is_payin_active=true."
        ),
    )


class BotTerminalLimits(BaseSchema):
    """Per-terminal limits view for the merchant Telegram bot."""
    id: int
    name: Optional[str] = None
    currency: str
    methods: List[BotMethodLimits] = Field(default_factory=list)


class BotLimitsResponse(BaseSchema):
    terminals: List[BotTerminalLimits] = Field(default_factory=list)
