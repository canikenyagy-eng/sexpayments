from sqlalchemy import Boolean, Column, DateTime, Enum, ForeignKey, Integer, Numeric, UniqueConstraint
from sqlalchemy.orm import relationship

from app.common.enums.users import UserRole
from app.common.types import utcnow
from app.infrastructure.db.base import Base


class TeamleadLink(Base):
    """
    Stores the relationship between a teamlead and a merchant or trader,
    along with the individual commission percentages.
    """
    __tablename__ = "teamlead_links"
    __table_args__ = (
        UniqueConstraint(
            "teamlead_id",
            "linked_entity_type",
            "linked_entity_id",
            name="uq_teamlead_link_teamlead_entity",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    teamlead_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    linked_entity_type = Column(Enum(UserRole), nullable=False, index=True)
    linked_entity_id = Column(Integer, nullable=False, index=True)

    # Reward % on payin orders (legacy name kept for back-compat).
    fee_percent = Column(Numeric(5, 2), default=0, nullable=False)
    # Reward % on payouts — configured INDEPENDENTLY of the payin %, so a teamlead
    # can earn a different rate (or nothing, default 0) on payouts vs orders.
    payout_fee_percent = Column(Numeric(5, 2), default=0, nullable=False, server_default="0")

    is_active = Column(Boolean, default=True, nullable=False)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
