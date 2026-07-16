from sqlalchemy import BigInteger, Column, Integer, String, ForeignKey, Enum, Boolean, JSON, Table, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship
from app.infrastructure.db.base import Base
from app.common.enums.traders import TraderStatus
from app.common.enums.payments import PaymentMethod

trader_group_members = Table(
    "trader_group_members",
    Base.metadata,
    Column("trader_id", Integer, ForeignKey("traders.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", Integer, ForeignKey("trader_groups.id", ondelete="CASCADE"), primary_key=True),
)

merchant_trader_groups = Table(
    "merchant_trader_groups",
    Base.metadata,
    Column("merchant_id", Integer, ForeignKey("merchants.id", ondelete="CASCADE"), primary_key=True),
    Column("group_id", Integer, ForeignKey("trader_groups.id", ondelete="CASCADE"), primary_key=True),
)

trader_merchants = Table(
    "trader_merchants",
    Base.metadata,
    Column("trader_id", Integer, ForeignKey("traders.id", ondelete="CASCADE"), primary_key=True),
    Column("merchant_id", Integer, ForeignKey("merchants.id", ondelete="CASCADE"), primary_key=True),
)

class TraderGroup(Base):
    __tablename__ = "trader_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(String(255), nullable=True)
    
    traders = relationship("Trader", secondary=trader_group_members, back_populates="groups", lazy="selectin")
    merchants = relationship("Merchant", secondary=merchant_trader_groups, back_populates="trader_groups", lazy="selectin")


class Trader(Base):
    __tablename__ = "traders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    
    status = Column(Enum(TraderStatus), default=TraderStatus.DISABLED, nullable=False)
    
    is_payin_active = Column(Boolean, default=False, nullable=False)
    is_payout_active = Column(Boolean, default=False, nullable=False)

    telegram_group_id = Column(BigInteger, nullable=True)

    # When True the trader accepts orders from any merchant, ignoring direct/group bindings.
    accept_all_merchants = Column(Boolean, default=False, nullable=False)

    # Fixed withdrawal fee in USDT
    withdrawal_fee_fixed = Column(Numeric(15, 2), default=0, nullable=False)

    # When True, every receipt uploaded by a merchant on this trader's orders
    # is automatically run through the trader's default receipt-check provider.
    # Trader WORK balance is charged the configured per-check price.
    receipt_auto_check = Column(Boolean, default=False, nullable=False)

    # Trader's preferred receipt-check provider: pre-selected in the manual
    # "check receipt" modal and used for automatic checks. NULL → fall back to
    # the first active provider. SET NULL on provider delete so the default
    # clears gracefully instead of dangling.
    default_receipt_check_provider_id = Column(
        Integer,
        ForeignKey("receipt_check_providers.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Payout config: trader is credited amount + this fee % on completed payouts;
    # payout_hold_hours delays availability of those earnings (0 = off).
    payout_fee_percent = Column(Numeric(5, 2), default=0, nullable=False, server_default="0")
    payout_hold_hours = Column(Integer, default=0, nullable=False, server_default="0")
    # Receipt verification mode: NULL → use global default; True → trader's
    # receipt auto-completes; False → requires admin receipt check.
    payout_receipt_auto = Column(Boolean, nullable=True)

    # Materialized achievements bonus (percentage points), recomputed off the hot
    # path by the achievements worker (app/modules/achievements) and added to the
    # trader's method fee in OrderService._calculate_trader_fee (like PrimeTime).
    # Default 0 = no bonus / feature off.
    achievement_bonus_percent = Column(Numeric(5, 2), default=0, nullable=False, server_default="0")

    # Admin-set per-trader priority bonus (%). Scales the base of all his
    # requisites: base = 100 × (1 + priority_bonus_percent/100). Any value.
    priority_bonus_percent = Column(Numeric(10, 2), default=0, nullable=False, server_default="0")

    groups = relationship("TraderGroup", secondary=trader_group_members, back_populates="traders", lazy="selectin")
    merchants = relationship("Merchant", secondary=trader_merchants, back_populates="traders", lazy="selectin")
    method_configs = relationship("TraderMethodConfig", back_populates="trader", cascade="all, delete-orphan", lazy="selectin")

class TraderMethodConfig(Base):
    __tablename__ = "trader_method_configs"

    id = Column(Integer, primary_key=True, index=True)
    trader_id = Column(Integer, ForeignKey("traders.id", ondelete="CASCADE"), nullable=False, index=True)
    payment_method = Column(Enum(PaymentMethod), nullable=False, index=True) 
    
    fee = Column(Numeric(5, 2), nullable=False, default=0)
    min_amount = Column(Numeric(18, 2), nullable=False)
    max_amount = Column(Numeric(18, 2), nullable=False)
    
    is_active = Column(Boolean, default=True, nullable=False)

    trader = relationship("Trader", back_populates="method_configs")

    __table_args__ = (
        UniqueConstraint('trader_id', 'payment_method', name='uq_trader_payment_method'),
    )
