from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Text,
)

from app.common.enums.broadcasts import BroadcastAudience, BroadcastStatus
from app.common.types import utcnow
from app.infrastructure.db.base import Base


class Broadcast(Base):
    """One admin → all-traders broadcast job (text sent via the trader bot).

    ``total_recipients`` is snapshotted at creation; ``delivered`` / ``failed``
    are filled in by the worker as it sends. The row is the source of the
    «История рассылок» table.
    """

    __tablename__ = "broadcasts"

    id = Column(Integer, primary_key=True, index=True)
    # Nulled out if the admin user is removed — the historic row survives.
    admin_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    text = Column(Text, nullable=False)
    audience = Column(
        Enum(BroadcastAudience, name="broadcastaudience", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    status = Column(
        Enum(BroadcastStatus, name="broadcaststatus", values_callable=lambda x: [e.value for e in x]),
        default=BroadcastStatus.PENDING,
        nullable=False,
        index=True,
    )

    total_recipients = Column(Integer, default=0, nullable=False)
    delivered = Column(Integer, default=0, nullable=False)
    failed = Column(Integer, default=0, nullable=False)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
