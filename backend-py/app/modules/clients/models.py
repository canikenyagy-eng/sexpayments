import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from app.common.types import utcnow
from app.infrastructure.db.base import Base


class Client(Base):
    """A unique merchant client — one row per ``(merchant_id, client_user_id)``.

    ``client_user_id`` is the merchant-supplied id (payin ``clientID`` / legacy
    ``userId``), already carried on ``orders.client_user_id``. Rows are
    MATERIALISED off the hot path by ``refresh_clients_task`` (rolls up orders);
    order creation never writes here. ``is_blocked`` is toggled only by the
    admin block/unblock action and mirrored into the Redis blocked-set that the
    hot-path ban check reads (see ``clients.block_cache``).
    """

    __tablename__ = "clients"
    __table_args__ = (
        UniqueConstraint("merchant_id", "client_user_id", name="uq_clients_merchant_client"),
    )

    id = Column(BigInteger, primary_key=True)
    public_id = Column(UUID(as_uuid=True), nullable=False, unique=True, index=True, default=uuid.uuid4)

    merchant_id = Column(Integer, ForeignKey("merchants.id"), nullable=False)
    client_user_id = Column(String(255), nullable=False)  # merchant's clientID / userId

    # Ban state — toggled only by the admin action.
    is_blocked = Column(Boolean, nullable=False, default=False)
    block_reason = Column(String(500), nullable=True)
    blocked_by_admin_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    blocked_at = Column(DateTime(timezone=True), nullable=True)

    # Activity window (maintained idempotently by the materialisation task:
    # first_seen stays the original, last_seen advances via GREATEST).
    first_seen_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=True)

    # Rollup stats (recomputed off the hot path by refresh_clients_task; order
    # creation never writes here). Conversion = successful/total is derived in
    # the API, not stored. turnover_usdt = Σ amount_usdt of SUCCESS orders.
    total_orders = Column(Integer, nullable=False, default=0, server_default="0")
    successful_orders = Column(Integer, nullable=False, default=0, server_default="0")
    turnover_usdt = Column(Numeric(15, 4), nullable=False, default=0, server_default="0")

    # Times we withheld a requisite because the client was blocked (ban hot-path
    # rejection). Counted via Redis on the rejected branch, folded in off the hot
    # path by refresh_clients_task; order creation never writes here.
    blocked_attempts = Column(Integer, nullable=False, default=0, server_default="0")

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
