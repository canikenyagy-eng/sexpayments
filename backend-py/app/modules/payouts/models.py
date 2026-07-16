import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutReceiptStatus, PayoutStatus
from app.common.types import utcnow
from app.infrastructure.db.base import Base


# ACL: which traders are allowed to serve a given payout terminal's pool.
payout_terminal_traders = Table(
    "payout_terminal_traders",
    Base.metadata,
    Column("payout_terminal_id", Integer, ForeignKey("payout_terminals.id", ondelete="CASCADE"), primary_key=True),
    Column("trader_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
)


class PayoutTerminal(Base):
    """A merchant's PAYOUT terminal — the payout-side analogue of ``Merchant``
    (the payin terminal). Owns its own API key, balance, rate and economics.

    A payout is created against a terminal (by the terminal's API key), funded
    from the terminal's own USDT balance (WORK→ESCROW freeze), and served only by
    traders bound to the terminal (``allowed_traders`` ACL). The owner (``user_id``)
    can have many payout terminals (and many payin merchants).

    Economics (all settled in USDT at the terminal's ``rate_config`` rate):
      * ``commission_percent`` — the merchant's price: charged amount + this %
        (e.g. 1000 @ 10% → merchant pays 1100-worth).
      * ``ttl_minutes`` — how long a payout lives before EXPIRED (+ refund);
        a per-payout override may shorten/lengthen it.
      * ``receipts_to_close`` — max number of partial receipts allowed to close a
        payout (1 = single full payment, as before).
    """

    __tablename__ = "payout_terminals"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)  # owner
    name = Column(String(255), nullable=False)

    status = Column(
        Enum(TerminalStatus, name="payout_terminal_status", values_callable=lambda x: [e.value for e in x]),
        default=TerminalStatus.ENABLED,
        nullable=False,
    )
    currency = Column(Enum(Currency), default=Currency.RUB, nullable=False)

    # Auth (own key, separate from the payin merchant key)
    api_key = Column(String(255), nullable=False, unique=True, index=True)
    api_secret = Column(String(255), nullable=False)

    # Rate (fiat→USDT) — same mechanism as payin merchant.
    rate_config_id = Column(Integer, ForeignKey("rate_configs.id"), nullable=True)

    # Economics
    commission_percent = Column(Numeric(5, 2), default=0, nullable=False, server_default="0")
    ttl_minutes = Column(Integer, default=60, nullable=False, server_default="60")
    receipts_to_close = Column(Integer, default=1, nullable=False, server_default="1")
    min_amount = Column(Numeric(15, 4), nullable=True)
    max_amount = Column(Numeric(15, 4), nullable=True)

    # Callbacks
    webhook_url = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    # Relationships
    rate_config = relationship("RateConfig", foreign_keys=[rate_config_id], lazy="selectin")
    allowed_traders = relationship("User", secondary=payout_terminal_traders, lazy="selectin")


class Payout(Base):
    """A merchant-requested payout to an end-user, fulfilled by a trader.

    Lifecycle: CREATED (terminal pool) → CLAIMED (trader takes it exclusively) →
    [AWAITING_CHECK] → COMPLETED. Money settles in USDT at the terminal's rate:
    at creation the terminal's WORK→ESCROW is frozen for amount_usdt +
    merchant_fee_usdt; on completion the terminal ESCROW pays the trader
    (reimburse amount) and the platform (commission), and the platform pays the
    trader's fee. The destination requisite (``req_*``) is the END-USER's and is
    hidden from traders until they claim the payout.
    """

    __tablename__ = "payouts"

    # Identity
    id = Column(Integer, primary_key=True, index=True)
    uuid = Column(UUID(as_uuid=True), default=uuid.uuid4, unique=True, index=True, nullable=False)
    external_id = Column(String(100), nullable=False, index=True)  # merchant's payout id

    # Participants
    # Nullable because a "долив" (requisite refill) is a payout with NO terminal —
    # it is funded by the requesting trader and lands on that trader's OWN payin
    # requisite. See the ``is_doliv`` block below.
    payout_terminal_id = Column(Integer, ForeignKey("payout_terminals.id"), nullable=True, index=True)
    trader_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # claimer (доливщик for долив)
    client_user_id = Column(String(255), nullable=True)  # merchant's end-user id

    # Typing
    payment_method = Column(Enum(PaymentMethod), nullable=False, index=True)
    payment_option_id = Column(Integer, ForeignKey("payment_options.id"), nullable=True)

    # Money (USDT settlement at the terminal's rate)
    amount = Column(Numeric(15, 4), nullable=False)  # fiat amount to send to the end-user
    currency = Column(Enum(Currency), nullable=False)
    amount_usdt = Column(Numeric(15, 4), nullable=True)
    exchange_rate = Column(Numeric(15, 4), nullable=True)
    merchant_fee_usdt = Column(Numeric(15, 4), nullable=True)  # commission charged to merchant
    trader_fee_usdt = Column(Numeric(15, 4), nullable=True)    # trader reward (set at claim); доливщик reward for долив

    # ─── Долив (requisite refill) ───
    # A долив is a payout a trader requests against their OWN under-used payin
    # requisite: the requesting trader funds it (WORK→ESCROW freeze of
    # amount_usdt + doliv_price_usdt) and a "доливщик" (from the platform-settings
    # executor list) fulfils it by really sending the money to the card. On
    # completion the requesting trader is debited, the доливщик gets the amount
    # back + a reward (``trader_fee_usdt``), and the requisite's turnover is
    # filled by ``amount``. ``payout_terminal_id`` is NULL for доливы.
    is_doliv = Column(Boolean, default=False, nullable=False, server_default="false", index=True)
    requester_trader_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)  # who funds the долив
    refill_order_id = Column(Integer, ForeignKey("orders.id"), nullable=True)        # payin order it was called on
    refill_requisite_id = Column(Integer, ForeignKey("requisites.id"), nullable=True)  # requisite whose turnover fills
    doliv_price_usdt = Column(Numeric(15, 4), nullable=True)  # доliv price the requester pays (analog of merchant_fee)

    # Destination requisite (provided by merchant; hidden until claimed)
    req_holder = Column(String(255), nullable=False)   # account holder name
    req_number = Column(String(255), nullable=False)   # card / account number
    req_extra = Column(String(255), nullable=True)     # bank / extra info

    # Status
    status = Column(Enum(PayoutStatus), default=PayoutStatus.CREATED, nullable=False, index=True)
    rejection_reason = Column(Text, nullable=True)

    # Claim (exclusive)
    claimed_at = Column(DateTime(timezone=True), nullable=True)
    claim_expires_at = Column(DateTime(timezone=True), nullable=True)  # claim_ttl → auto-return to pool

    # Receipt (proof of execution by the trader). Single-receipt fields kept for
    # back-compat; multi-receipt partial payments live in ``PayoutReceipt`` rows.
    receipt_file = Column(String(255), nullable=True)
    receipt_uploaded_at = Column(DateTime(timezone=True), nullable=True)

    # Trader earnings hold (optional, per-trader X hours)
    trader_hold_until = Column(DateTime(timezone=True), nullable=True)
    hold_released_at = Column(DateTime(timezone=True), nullable=True)

    # Callbacks
    webhook_url = Column(String(255), nullable=True)  # per-payout override; else terminal default

    # Timestamps
    expires_at = Column(DateTime(timezone=True), nullable=True)  # terminal TTL deadline
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    canceled_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    terminal = relationship("PayoutTerminal", foreign_keys=[payout_terminal_id], lazy="selectin")
    payment_option = relationship("PaymentOption", foreign_keys=[payment_option_id], lazy="selectin")

    __table_args__ = (
        # Idempotency: one payout per (terminal, external_id).
        UniqueConstraint("payout_terminal_id", "external_id", name="uq_payout_terminal_external"),
    )


class PayoutReceipt(Base):
    """One partial payment of a payout (proof + moderation state).

    A payout may be closed by up to ``terminal.receipts_to_close`` receipts. Each
    carries its own fiat ``amount``; the APPROVED amounts must sum to EXACTLY the
    payout amount (no over/under) for the payout to COMPLETE. Each receipt is
    moderated individually — a REJECTED one is voided and the trader re-uploads
    that installment. Money settles once, on the full amount, at completion.
    """

    __tablename__ = "payout_receipts"

    id = Column(Integer, primary_key=True, index=True)
    payout_id = Column(Integer, ForeignKey("payouts.id", ondelete="CASCADE"), nullable=False, index=True)
    trader_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    amount = Column(Numeric(15, 4), nullable=False)   # fiat, partial payment
    file = Column(String(255), nullable=False)

    status = Column(Enum(PayoutReceiptStatus), default=PayoutReceiptStatus.PENDING, nullable=False, index=True)
    rejection_reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    moderated_at = Column(DateTime(timezone=True), nullable=True)
    moderated_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    payout = relationship("Payout", foreign_keys=[payout_id], lazy="selectin")
