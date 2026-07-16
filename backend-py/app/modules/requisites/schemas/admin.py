from datetime import datetime
from decimal import Decimal
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.requisites import RequisiteStatus
from app.modules.base.schemas import BaseSchema, BaseResponseSchema
from app.modules.payments.schemas import PaymentOptionResponse


class _TraderLoginProxy(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    username: Optional[str] = None


class RequisiteLimitBase(BaseSchema):
    limit_daily: float = Field(100000.00, ge=0)
    limit_monthly: float = Field(1000000.00, ge=0)
    limit_min_transaction: float = Field(100.00, ge=0)
    limit_max_transaction: float = Field(50000.00, ge=0)
    limit_max_concurrent_orders: Optional[int] = Field(None, ge=1)
    # Single reset toggle — when True, both daily and monthly counters reset
    # at midnight UTC / 1st of month. When False (default), the counters
    # accumulate as a single lifetime/общий limit.
    reset_enabled: bool = False


class RequisiteLimitUpdate(BaseSchema):
    limit_daily: Optional[float] = Field(None, ge=0)
    limit_monthly: Optional[float] = Field(None, ge=0)
    limit_min_transaction: Optional[float] = Field(None, ge=0)
    limit_max_transaction: Optional[float] = Field(None, ge=0)
    limit_max_concurrent_orders: Optional[int] = Field(None, ge=1)
    reset_enabled: Optional[bool] = None

    current_daily_turnover: Optional[float] = Field(
        None, ge=0, le=0,
        description="Только 0 — сброс дневного счётчика оборота.",
    )
    current_monthly_turnover: Optional[float] = Field(
        None, ge=0, le=0,
        description="Только 0 — сброс месячного счётчика оборота.",
    )


class RequisiteLimitResponse(BaseResponseSchema, RequisiteLimitBase):
    id: int
    requisite_id: int
    current_daily_turnover: float
    current_monthly_turnover: float
    last_reset_at: Optional[datetime] = None
    # Sum of `amount` across orders with status PENDING / RECEIPT_UPLOADED on
    # this requisite — populated at list-time so the UI can show pending
    # volume next to completed turnover on the gauge.
    active_amount: float = 0.0
    updated_at: Optional[datetime]


class RequisiteCreate(BaseSchema):
    nickname: Optional[str] = Field(None, max_length=100)
    payment_option_id: int = Field(..., description="ID банка/опции оплаты из справочника payment_options")
    account_number: str = Field(..., min_length=5, max_length=100)
    account_holder: str = Field(..., min_length=2, max_length=255)
    payment_method: PaymentMethod
    currency: Optional[Currency] = Field(None, description="Если не указано — берётся из выбранного PaymentOption")
    limits: Optional[RequisiteLimitBase] = None
    trader_priority: int = Field(1, ge=1, le=3, description="Приоритет реквизита (1–3)")


class RequisiteUpdate(BaseSchema):
    nickname: Optional[str] = Field(None, max_length=100)
    payment_option_id: Optional[int] = Field(None, description="Изменить банк (опцию оплаты)")
    account_number: Optional[str] = Field(None, min_length=5, max_length=100)
    account_holder: Optional[str] = Field(None, min_length=2, max_length=255)
    payment_method: Optional[PaymentMethod] = None
    status: Optional[RequisiteStatus] = None
    is_active: Optional[bool] = None
    is_archived: Optional[bool] = None
    limits: Optional[RequisiteLimitUpdate] = None
    trader_priority: Optional[int] = Field(None, ge=1, le=3, description="Приоритет реквизита (1–3)")


class RequisiteResponse(BaseResponseSchema):
    id: int
    trader_id: int
    nickname: Optional[str] = None
    bank_name: str
    account_number: str
    account_holder: str
    payment_method: PaymentMethod
    currency: Currency
    payment_option_id: Optional[int] = None
    payment_option: Optional[PaymentOptionResponse] = None
    status: RequisiteStatus
    is_active: bool
    is_archived: bool
    trader_priority: int
    priority_score: Decimal  # computed weight for the WEIGHTED pooling strategy
    last_used_at: Optional[datetime]
    limits: Optional[RequisiteLimitResponse]
    trader: Optional[_TraderLoginProxy] = Field(default=None, exclude=True)

    @computed_field  # type: ignore[misc]
    @property
    def trader_login(self) -> Optional[str]:
        return self.trader.username if self.trader else None
