import uuid

from sqlalchemy import JSON, Column, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.enums.finances import Currency
from app.common.enums.orders import OrderSource, OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.receipt_moderations import ModerationStatus
from app.common.types import utcnow
from app.infrastructure.db.base import Base


class Order(Base):
    __tablename__ = "orders"

    # Идентификация
    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, index=True, nullable=False)
    external_id = Column(String(100), nullable=False, index=True)  # client_transaction_id
    
    # Участники
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False)
    trader_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    requisite_id = Column(Integer, ForeignKey("requisites.id"), nullable=True)
    # Provider's order id for cascade-routed deals (the won attempt's
    # external_order_id). Non-null ⇒ the deal went through a cascade provider.
    provider_order_id = Column(String(255), nullable=True)
    client_user_id = Column(String(255), nullable=True)  # ID клиента в системе мерчанта
    
    # Канал создания
    source = Column(
        Enum(OrderSource, name="ordersource", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=OrderSource.API,
    )

    # Типизация
    direction = Column(Enum(PaymentDirection), default=PaymentDirection.PAYIN, nullable=False)
    payment_method = Column(Enum(PaymentMethod), nullable=False)
    payment_option_id = Column(Integer, ForeignKey("payment_options.id"), nullable=True)
    
    # Финансы
    amount = Column(Numeric(15, 4), nullable=False)  # Сумма в фиате
    currency = Column(Enum(Currency), nullable=False)
    amount_usdt = Column(Numeric(15, 4), nullable=True)
    exchange_rate = Column(Numeric(15, 4), nullable=True)
    fee_usdt = Column(Numeric(15, 4), nullable=True)          # merchant commission captured
    trader_fee_usdt = Column(Numeric(15, 4), nullable=True)   # trader reward
    profit_usdt = Column(Numeric(15, 4), nullable=True)       # merchant net (amount − fee)

    # Denormalized financial snapshot (projection of the ledger; the ledger
    # stays the single source of truth). Maintained by OrderService on every
    # status / amount transition. teamlead_reward + platform_profit are
    # finalized at SUCCESS and zeroed when settlement is reversed (reject /
    # refund); ``financials`` carries the per-teamlead breakdown + the headline
    # figures for one-shot reads / reporting.
    teamlead_reward_usdt = Column(Numeric(15, 4), nullable=True)   # Σ teamlead rewards paid
    platform_profit_usdt = Column(Numeric(15, 4), nullable=True)   # fee − trader_fee − teamlead_reward
    financials = Column(JSON, nullable=True)

    # Статусы
    status = Column(Enum(OrderStatus), default=OrderStatus.CREATED, nullable=False)
    
    # Работа с чеками
    receipt_file = Column(String(255), nullable=True)
    receipt_uploaded_at = Column(DateTime(timezone=True), nullable=True)
    receipt_uploaded_by = Column(String(50), nullable=True)  # 'merchant', 'system', 'trader'

    # Premoderation state (support-bot pre-trader review). NONE for the
    # default flow where the receipt goes straight to the trader; PENDING
    # while the support-bot waits for an admin click; one of the terminal
    # statuses once an admin decides. See ``app.common.enums.receipt_moderations``.
    moderation_status = Column(
        Enum(ModerationStatus, name="moderationstatus", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ModerationStatus.NONE,
        server_default=ModerationStatus.NONE.value,
    )

    # Webhook & Payment URL
    webhook_url = Column(String(255), nullable=True)
    payment_url = Column(String(255), nullable=True)

    # Временные метки
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    date_end = Column(DateTime(timezone=True), nullable=True)  # Дедлайн для оплаты
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    rejected_at = Column(DateTime(timezone=True), nullable=True)
    
    # Прочее
    fingerprint = Column(String(255), nullable=True)
    trader_comment = Column(Text, nullable=True)
    rejection_reason = Column(Text, nullable=True)

    # Relationships
    requisite = relationship("Requisite", foreign_keys=[requisite_id], lazy="selectin")
    payment_option = relationship("PaymentOption", foreign_keys=[payment_option_id], lazy="selectin")
    merchant = relationship("Merchant", foreign_keys=[merchant_id], lazy="selectin")


class OrderStatusHistory(Base):
    __tablename__ = "order_status_history"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    
    old_status = Column(Enum(OrderStatus), nullable=True)
    new_status = Column(Enum(OrderStatus), nullable=False)
    
    changed_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
