from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, UniqueConstraint

from app.common.types import utcnow
from app.infrastructure.db.base import Base


class StatsSnapshot(Base):
    __tablename__ = "stats_snapshots"

    id = Column(Integer, primary_key=True, index=True)

    turnover_usdt = Column(Numeric(20, 4), default=0, nullable=False)
    profit_usdt = Column(Numeric(20, 4), default=0, nullable=False)
    requests_rub = Column(Numeric(20, 4), default=0, nullable=False)
    orders_total = Column(Integer, default=0, nullable=False)
    orders_success = Column(Integer, default=0, nullable=False)
    orders_active = Column(Integer, default=0, nullable=False)
    payout_count = Column(Integer, default=0, nullable=False)
    payout_pct = Column(Numeric(8, 4), default=0, nullable=False)
    payin_requests_total = Column(Integer, default=0, nullable=False)
    payin_requests_24h = Column(Integer, default=0, nullable=False)
    requests_rub_24h = Column(Numeric(20, 4), default=0, nullable=False)
    conversion_pct = Column(Numeric(8, 4), default=0, nullable=False)
    merchants_online_24h = Column(Integer, default=0, nullable=False)
    traders_online_24h = Column(Integer, default=0, nullable=False)
    pending_withdrawals = Column(Integer, default=0, nullable=False)
    active_disputes = Column(Integer, default=0, nullable=False)

    data_cutoff_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class MerchantStatsSnapshot(Base):
    __tablename__ = "merchant_stats_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False, index=True)

    turnover_usdt = Column(Numeric(20, 4), default=0, nullable=False)
    fee_usdt = Column(Numeric(20, 4), default=0, nullable=False)
    orders_total = Column(Integer, default=0, nullable=False)
    orders_success = Column(Integer, default=0, nullable=False)
    orders_active = Column(Integer, default=0, nullable=False)
    orders_failed = Column(Integer, default=0, nullable=False)
    conversion_pct = Column(Numeric(8, 4), default=0, nullable=False)
    pending_withdrawals = Column(Integer, default=0, nullable=False)
    active_disputes = Column(Integer, default=0, nullable=False)
    requests_rub = Column(Numeric(20, 4), default=0, nullable=False, server_default="0")
    payin_requests_total = Column(Integer, default=0, nullable=False, server_default="0")

    data_cutoff_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("merchant_id", name="uq_merchant_stats_snapshot"),
    )
