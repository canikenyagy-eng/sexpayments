from sqlalchemy import Column, DateTime, String, Integer, Text, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB

from app.common.types import utcnow
from app.infrastructure.db.base import Base

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=True, index=True)
    action = Column(String, nullable=False, index=True)
    entity_type = Column(String, nullable=False)
    entity_id = Column(String, nullable=False)
    old_values = Column(JSONB, nullable=True)
    new_values = Column(JSONB, nullable=True)
    request_id = Column(String, nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)

class MerchantApiLog(Base):
    __tablename__ = "merchant_api_logs"

    id = Column(Integer, primary_key=True, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id", ondelete="SET NULL"), nullable=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True)

    url = Column(String(255), nullable=False)
    method = Column(String(10), nullable=False)

    request_headers = Column(JSONB, nullable=True)
    request_body = Column(Text, nullable=True)

    response_status = Column(Integer, nullable=True)
    response_headers = Column(JSONB, nullable=True)
    response_body = Column(Text, nullable=True)
    response_time_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class OrderCreationSnapshot(Base):
    __tablename__ = "order_creation_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    api_log_id = Column(
        Integer,
        ForeignKey("merchant_api_logs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )

    request_data = Column(JSONB, nullable=False)
    merchant_snapshot = Column(JSONB, nullable=False)
    rate_snapshot = Column(JSONB, nullable=True)
    traders_snapshot = Column(JSONB, nullable=False)
    candidates = Column(JSONB, nullable=False)
    result = Column(JSONB, nullable=False)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
