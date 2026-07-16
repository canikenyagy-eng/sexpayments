"""payouts: merchant payout pool + trader claim/execute

Revision ID: 042
Revises: 041
Create Date: 2026-06-06

Adds the merchant **payout** feature (separate from payin orders and from
self-service withdrawal_requests):
  * payouts — one row per merchant payout request; lifecycle CREATED (pool) →
    CLAIMED → [AWAITING_CHECK] → COMPLETED / CANCELED / EXPIRED.
  * merchants.payout_commission_percent / payout_ttl_seconds — per-merchant config.
  * traders.payout_fee_percent / payout_hold_hours / payout_receipt_auto — per-trader config.
  * callback_attempts.payout_id — generalize merchant callbacks to payouts.

USDT settlement mirrors payin (amount_usdt / exchange_rate / *_fee_usdt). The
``payoutstatus`` enum stores UPPERCASE member NAMES (same convention as
orderstatus). Reuses the existing ``currency`` / ``paymentmethod`` enum types
(create_type=False). Idempotent — re-appliable across partially-upgraded envs.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "042"
down_revision = "041"
branch_labels = None
depends_on = None


_PAYOUT_STATUS = postgresql.ENUM(
    "CREATED", "CLAIMED", "AWAITING_CHECK", "COMPLETED", "CANCELED", "EXPIRED",
    name="payoutstatus",
    create_type=False,
)
_CURRENCY = postgresql.ENUM(name="currency", create_type=False)
_PAYMENT_METHOD = postgresql.ENUM(name="paymentmethod", create_type=False)


def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _PAYOUT_STATUS.create(bind=bind, checkfirst=True)

    # --- payouts table ---
    if not _has_table(inspector, "payouts"):
        op.create_table(
            "payouts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("uuid", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("external_id", sa.String(length=100), nullable=False),
            sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("merchants.id"), nullable=False),
            sa.Column("trader_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("client_user_id", sa.String(length=255), nullable=True),
            sa.Column("payment_method", _PAYMENT_METHOD, nullable=False),
            sa.Column("payment_option_id", sa.Integer(), sa.ForeignKey("payment_options.id"), nullable=True),
            sa.Column("amount", sa.Numeric(15, 4), nullable=False),
            sa.Column("currency", _CURRENCY, nullable=False),
            sa.Column("amount_usdt", sa.Numeric(15, 4), nullable=True),
            sa.Column("exchange_rate", sa.Numeric(15, 4), nullable=True),
            sa.Column("merchant_fee_usdt", sa.Numeric(15, 4), nullable=True),
            sa.Column("trader_fee_usdt", sa.Numeric(15, 4), nullable=True),
            sa.Column("req_holder", sa.String(length=255), nullable=False),
            sa.Column("req_number", sa.String(length=255), nullable=False),
            sa.Column("req_extra", sa.String(length=255), nullable=True),
            sa.Column("status", _PAYOUT_STATUS, nullable=False, server_default="CREATED"),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("receipt_file", sa.String(length=255), nullable=True),
            sa.Column("receipt_uploaded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("trader_hold_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("hold_released_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("webhook_url", sa.String(length=255), nullable=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("merchant_id", "external_id", name="uq_payout_merchant_external"),
        )
        op.create_index("ix_payouts_uuid", "payouts", ["uuid"], unique=True)
        op.create_index("ix_payouts_external_id", "payouts", ["external_id"])
        op.create_index("ix_payouts_merchant_id", "payouts", ["merchant_id"])
        op.create_index("ix_payouts_trader_id", "payouts", ["trader_id"])
        op.create_index("ix_payouts_status", "payouts", ["status"])
        op.create_index("ix_payouts_expires_at", "payouts", ["expires_at"])

    # --- merchant payout config ---
    if not _has_column(inspector, "merchants", "payout_commission_percent"):
        op.add_column("merchants", sa.Column(
            "payout_commission_percent", sa.Numeric(5, 2), nullable=False, server_default="0"))
    if not _has_column(inspector, "merchants", "payout_ttl_seconds"):
        op.add_column("merchants", sa.Column(
            "payout_ttl_seconds", sa.Integer(), nullable=False, server_default="3600"))

    # --- trader payout config ---
    if not _has_column(inspector, "traders", "payout_fee_percent"):
        op.add_column("traders", sa.Column(
            "payout_fee_percent", sa.Numeric(5, 2), nullable=False, server_default="0"))
    if not _has_column(inspector, "traders", "payout_hold_hours"):
        op.add_column("traders", sa.Column(
            "payout_hold_hours", sa.Integer(), nullable=False, server_default="0"))
    if not _has_column(inspector, "traders", "payout_receipt_auto"):
        op.add_column("traders", sa.Column(
            "payout_receipt_auto", sa.Boolean(), nullable=True))

    # --- generalize callbacks to payouts ---
    if _has_table(inspector, "callback_attempts") and not _has_column(inspector, "callback_attempts", "payout_id"):
        op.add_column("callback_attempts", sa.Column(
            "payout_id", sa.Integer(), sa.ForeignKey("payouts.id", ondelete="CASCADE"), nullable=True))
        op.create_index("ix_callback_attempts_payout_id", "callback_attempts", ["payout_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "callback_attempts", "payout_id"):
        op.drop_index("ix_callback_attempts_payout_id", table_name="callback_attempts")
        op.drop_column("callback_attempts", "payout_id")

    for col in ("payout_receipt_auto", "payout_hold_hours", "payout_fee_percent"):
        if _has_column(inspector, "traders", col):
            op.drop_column("traders", col)
    for col in ("payout_ttl_seconds", "payout_commission_percent"):
        if _has_column(inspector, "merchants", col):
            op.drop_column("merchants", col)

    if _has_table(inspector, "payouts"):
        op.drop_table("payouts")

    op.execute(sa.text("DROP TYPE IF EXISTS payoutstatus"))
