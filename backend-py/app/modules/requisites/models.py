from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, Numeric, String
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from app.infrastructure.db.base import Base
from app.common.enums.cascading import RequisiteSource
from app.common.enums.finances import Currency
from app.common.enums.requisites import RequisiteStatus
from app.common.enums.payments import PaymentMethod

class Requisite(Base):
    __tablename__ = "requisites"

    id = Column(Integer, primary_key=True, index=True)
    trader_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    payment_option_id = Column(Integer, ForeignKey("payment_options.id"), nullable=True, index=True)
    
    nickname = Column(String(100), nullable=True)
    bank_name = Column(String(100), nullable=False)
    account_number = Column(String(100), nullable=False)
    account_holder = Column(String(255), nullable=False)
    payment_method = Column(Enum(PaymentMethod), nullable=False)
    
    status = Column(Enum(RequisiteStatus), default=RequisiteStatus.DISABLED, nullable=False)
    status_updated_at = Column(DateTime(timezone=True), default=func.now(), nullable=False)
    currency = Column(Enum(Currency), default=Currency.RUB, nullable=False)

    is_active = Column(Boolean, default=True)
    is_archived = Column(Boolean, default=False, nullable=False)

    # Trader-set weight (1–3) + computed score for the WEIGHTED pooling strategy.
    # priority_score is recomputed per (trader, currency, method) group by
    # PriorityService — order creation only reads it. See docs spec.
    trader_priority = Column(Integer, nullable=False, default=1, server_default="1")
    priority_score = Column(Numeric(12, 4), nullable=False, default=100, server_default="100")

    last_used_at = Column(DateTime(timezone=True), nullable=True)

    # Source: 'local' for trader-owned requisites, 'cascade' for one-shot
    # requisites returned by external providers. Cascade requisites bypass
    # PoolingService entirely — they're only ever pinned to a single order.
    source = Column(
        Enum(RequisiteSource, name="requisitesource", values_callable=lambda x: [e.value for e in x]),
        default=RequisiteSource.LOCAL,
        nullable=False,
        server_default=RequisiteSource.LOCAL.value,
    )

    limits = relationship("RequisiteLimit", back_populates="requisite", uselist=False, lazy="selectin")
    trader = relationship("User", foreign_keys=[trader_id], lazy="selectin")
    payment_option = relationship("PaymentOption", foreign_keys=[payment_option_id], lazy="selectin")

class RequisiteLimit(Base):
    __tablename__ = "requisite_limits"

    id = Column(Integer, primary_key=True, index=True)
    requisite_id = Column(Integer, ForeignKey("requisites.id"), nullable=False, unique=True)

    # Лимиты
    limit_daily = Column(Numeric(15, 2), nullable=False, default=100000.00)
    limit_monthly = Column(Numeric(15, 2), nullable=False, default=1000000.00)
    limit_min_transaction = Column(Numeric(15, 2), nullable=False, default=100.00)
    limit_max_transaction = Column(Numeric(15, 2), nullable=False, default=50000.00)
    limit_max_concurrent_orders = Column(Integer, nullable=True, default=None)

    # Текущие обороты (счетчики)
    current_daily_turnover = Column(Numeric(15, 2), nullable=False, default=0.00)
    current_monthly_turnover = Column(Numeric(15, 2), nullable=False, default=0.00)

    # Опциональный авто-сброс счётчиков. По умолчанию выключено — счётчики
    # копятся на всё время жизни реквизита (общий лимит). Когда флаг включён,
    # фоновый celery-beat task ``reset_requisite_limits_task`` обнуляет
    # дневной счётчик каждую полночь UTC и месячный — в начале каждого месяца.
    reset_enabled = Column(Boolean, nullable=False, default=False, server_default="false")
    last_reset_at = Column(DateTime(timezone=True), nullable=True)

    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    requisite = relationship("Requisite", back_populates="limits")
