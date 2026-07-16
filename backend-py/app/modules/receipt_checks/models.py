from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB

from app.common.enums.receipt_checks import ReceiptCheckStatus, ReceiptCheckTrigger
from app.common.types import utcnow
from app.infrastructure.db.base import Base


class ReceiptCheckProvider(Base):
    """Third-party receipt verification provider (TREXO, future others).

    The service layer enforces a single active provider at a time; the table
    still allows multiple rows so admins can keep an inactive fallback
    configured.
    """

    __tablename__ = "receipt_check_providers"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    adapter_type = Column(String(64), nullable=False)  # "trexo", future codes
    is_active = Column(Boolean, default=False, nullable=False, index=True)

    base_url = Column(String(512), nullable=False)
    api_key_encrypted = Column(String(1024), nullable=True)
    # Last 4 chars of plaintext key, captured at create/update so the UI
    # can render `sk_live_***abcd` without decrypting.
    api_key_tail = Column(String(8), nullable=True)

    price_usdt = Column(Numeric(15, 4), default=0, nullable=False)
    request_timeout_ms = Column(Integer, default=90000, nullable=False)

    # Reserved for adapter-specific options (per-method overrides, etc.).
    settings = Column(JSONB, default=dict, nullable=False)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class ReceiptCheck(Base):
    """One verification attempt for a receipt file.

    Rows are immutable after `finished_at` is set. Cache lookup is by
    (order_id, file_sha256): if a successful or cached row exists for the
    same file on the same order we reuse the verdict instead of charging
    the trader again.
    """

    __tablename__ = "receipt_checks"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    provider_id = Column(
        Integer, ForeignKey("receipt_check_providers.id", ondelete="SET NULL"), nullable=True
    )
    trader_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    trigger = Column(String(16), nullable=False)  # ReceiptCheckTrigger
    status = Column(
        Enum(ReceiptCheckStatus, name="receiptcheckstatus", values_callable=lambda x: [e.value for e in x]),
        default=ReceiptCheckStatus.PENDING,
        nullable=False,
        index=True,
    )

    file_path = Column(String(512), nullable=False)
    file_sha256 = Column(String(64), nullable=False, index=True)

    is_clean = Column(Boolean, nullable=True)
    verdict = Column(JSONB, nullable=True)
    parsed_data = Column(JSONB, nullable=True)

    provider_check_id = Column(String(64), nullable=True)
    provider_tx_id = Column(String(128), nullable=True)
    raw_response = Column(JSONB, nullable=True)

    error_code = Column(String(64), nullable=True)
    error_message = Column(Text, nullable=True)

    price_usdt = Column(Numeric(15, 4), default=0, nullable=False)
    charged = Column(Boolean, default=False, nullable=False)
    refunded = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
