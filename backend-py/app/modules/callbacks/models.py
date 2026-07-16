from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB

from app.common.types import utcnow
from app.infrastructure.db.base import Base


class CallbackAttempt(Base):
    __tablename__ = "callback_attempts"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), nullable=False, index=True)

    url = Column(String(255), nullable=False)
    request_headers = Column(JSONB, nullable=True)
    request_payload = Column(JSONB, nullable=False)

    response_status = Column(Integer, nullable=True)
    response_headers = Column(JSONB, nullable=True)
    response_body = Column(Text, nullable=True)
    
    is_successful = Column(Boolean, default=False, nullable=False)
    attempt_number = Column(Integer, default=1, nullable=False)
    
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
