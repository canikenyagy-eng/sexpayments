from datetime import datetime
from typing import List, Optional
from pydantic import Field

from app.common.enums.finances import Currency
from app.common.enums.rates import OrderBookSide, RateSource
from app.modules.base.schemas import BaseSchema


class RateConfigCreate(BaseSchema):
    name: str
    source: RateSource = RateSource.BYBIT
    side: OrderBookSide
    position: int = Field(default=1, ge=1, description="Position in the order book (e.g., 1 for top 1)")
    payment_methods: List[str] = Field(default_factory=list, description="List of payment method IDs")
    fiat_currency: Currency
    crypto_currency: str = "USDT"
    update_interval_seconds: int = Field(default=60, ge=10)
    is_active: bool = True


class RateConfigUpdate(BaseSchema):
    name: Optional[str] = None
    side: Optional[OrderBookSide] = None
    position: Optional[int] = Field(None, ge=1)
    payment_methods: Optional[List[str]] = None
    update_interval_seconds: Optional[int] = Field(None, ge=10)
    is_active: Optional[bool] = None


class RateConfigResponse(RateConfigCreate):
    id: int
    current_rate: Optional[float]
    last_updated_at: Optional[datetime]
