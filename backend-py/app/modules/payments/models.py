from sqlalchemy import Boolean, Column, Enum, Integer, JSON, String
from app.common.enums.finances import Currency
from app.infrastructure.db.base import Base


class PaymentOption(Base):
    __tablename__ = "payment_options"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False)  # e.g., "Sberbank", "Tinkoff"
    logo_url = Column(String(255), nullable=True)
    
    # Store list of supported payment methods (e.g., ["sbp", "card"])
    supported_methods = Column(JSON, nullable=False, default=list)
    
    currency = Column(Enum(Currency), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
