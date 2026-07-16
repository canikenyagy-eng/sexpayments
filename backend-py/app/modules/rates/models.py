from datetime import datetime
from sqlalchemy import Boolean, Column, DateTime, Enum, Float, Integer, JSON, String
from app.infrastructure.db.base import Base
from app.common.enums.finances import Currency
from app.common.enums.rates import OrderBookSide, RateSource

class RateConfig(Base):
    __tablename__ = "rate_configs"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    
    source = Column(Enum(RateSource), nullable=False, default=RateSource.BYBIT)
    side = Column(Enum(OrderBookSide), nullable=False)
    position = Column(Integer, nullable=False, default=1) # 1 for Top 1, 2 for Top 2, etc.
    payment_methods = Column(JSON, nullable=False, default=lambda: []) # e.g., ["75"] for LocalCard(Green)
    
    fiat_currency = Column(Enum(Currency), nullable=False)
    crypto_currency = Column(String(10), nullable=False, default="USDT")
    
    update_interval_seconds = Column(Integer, nullable=False, default=60)
    is_active = Column(Boolean, nullable=False, default=True)
    
    current_rate = Column(Float, nullable=True)
    last_updated_at = Column(DateTime(timezone=True), nullable=True)
