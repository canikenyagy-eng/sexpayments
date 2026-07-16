"""Unified receipt store.

ONE table holding every uploaded receipt/proof file. A receipt always belongs
to an order (``order_id``) and is optionally attached to an appeal/dispute
(``dispute_id``) — appeal evidence is the same entity, just linked to a
dispute. One order → many receipts.

The legacy single ``orders.receipt_file`` column is kept as a mirror of the
latest receipt so existing single-file readers (cascade ``notify_receipt``,
the download endpoints, the current frontend) keep working unchanged.

Each receipt carries its OWN ``moderation_status`` — premoderation, the
trader-visibility gate and the auto-fraud check all run per receipt.
"""
import uuid

from sqlalchemy import BigInteger, Column, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID

from app.common.enums.receipt_moderations import ModerationDecision, ModerationStatus
from app.common.enums.receipts import ReceiptSource
from app.common.types import utcnow
from app.infrastructure.db.base import Base


class Receipt(Base):
    __tablename__ = "receipts"

    id = Column(Integer, primary_key=True)
    uuid = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, index=True, nullable=False)

    # Owner links — FK lives on the receipt (the "many" side). order_id is
    # mandatory; dispute_id is set when the receipt is appeal evidence.
    order_id = Column(Integer, ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True)
    dispute_id = Column(Integer, ForeignKey("disputes.id", ondelete="SET NULL"), nullable=True, index=True)

    file_path = Column(String(255), nullable=False)
    sha256 = Column(String(64), nullable=True, index=True)  # dedup + fraud-check key
    file_size = Column(Integer, nullable=True)
    mime = Column(String(100), nullable=True)

    source = Column(
        Enum(ReceiptSource, name="receiptsource", values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ReceiptSource.MERCHANT_API,
    )
    uploaded_by = Column(String(50), nullable=True)  # 'merchant' | 'system' | 'trader'

    # Per-receipt premoderation state. ``moderationstatus`` PG enum already
    # exists (orders.moderation_status) — reuse it, don't re-emit DDL.
    moderation_status = Column(
        Enum(
            ModerationStatus,
            name="moderationstatus",
            values_callable=lambda x: [e.value for e in x],
            create_type=False,
        ),
        nullable=False,
        default=ModerationStatus.NONE,
        server_default=ModerationStatus.NONE.value,
    )

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class ReceiptModeration(Base):
    """Receipt-moderation log — one row per moderation cycle for an order's
    uploaded receipt (the human-review leg of the moderation pipeline).

      * created when the receipt arrives and the premoderation flag is on
      * ``chat_id`` is captured at creation time so the historical row stays
        accurate even if the admin later switches the support-bot to a
        different chat via ``PlatformSetting.support_bot_chat_id``
      * ``message_id`` is back-filled by the worker once the bot replies with
        the Telegram message_id it created — the bot uses it later to edit
        the inline keyboard from a callback handler
      * first admin click stamps ``decision`` / ``moderator_*`` / ``decided_at``;
        later clicks are rejected via the first-wins precondition in
        ``ReceiptModerationService.apply_decision``
    """

    __tablename__ = "receipt_moderations"

    id = Column(Integer, primary_key=True)
    order_id = Column(
        Integer,
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    chat_id = Column(BigInteger, nullable=False)
    message_id = Column(BigInteger, nullable=True)

    decision = Column(
        Enum(
            ModerationDecision,
            name="moderationdecision",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=True,
    )
    moderator_tg_id = Column(BigInteger, nullable=True)
    moderator_username = Column(String(64), nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=True)
    # Timestamp of the LAST "check is waiting" reminder the support-bot sent for
    # this still-undecided check. NULL = never reminded. The periodic reminder
    # task re-pings every ``premoderation_reminder_minutes`` while decision is None.
    reminded_at = Column(DateTime(timezone=True), nullable=True)
