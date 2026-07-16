import uuid

from sqlalchemy import Column, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.common.enums.disputes import DisputeReason, DisputeStatus, DisputeSubstatus
from app.common.enums.users import UserRole
from app.common.types import utcnow
from app.infrastructure.db.base import Base


class Dispute(Base):
    __tablename__ = "disputes"

    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, index=True, nullable=False)
    
    order_id = Column(Integer, ForeignKey("orders.id"), unique=True, nullable=False, index=True)
    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False, index=True)
    
    initiator_type = Column(Enum(UserRole), nullable=False)
    initiator_id = Column(Integer, nullable=True)  # ID of user or merchant depending on type
    
    status = Column(Enum(DisputeStatus), default=DisputeStatus.OPEN, nullable=False, index=True)
    reason = Column(Enum(DisputeReason), nullable=False)
    substatus = Column(
        Enum(DisputeSubstatus, name="disputesubstatus", values_callable=lambda x: [e.value for e in x]),
        nullable=True,
    )

    assigned_user_type = Column(Enum(UserRole), nullable=True)
    assigned_user_id = Column(Integer, nullable=True)
    
    resolution_text = Column(Text, nullable=True)
    resolved_by_type = Column(Enum(UserRole), nullable=True)
    resolved_by_id = Column(Integer, nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    
    evidence_files = Column(JSONB, nullable=True)  # Array of file paths

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
