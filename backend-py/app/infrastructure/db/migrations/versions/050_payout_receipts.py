"""payout receipts: partial-payment receipts for multi-receipt close

Revision ID: 050
Revises: 049
Create Date: 2026-06-17

A payout may be closed by up to ``terminal.receipts_to_close`` partial payments,
each its own ``PayoutReceipt`` (fiat amount + proof file + moderation state). The
APPROVED amounts must sum to EXACTLY the payout amount for it to COMPLETE; money
still settles once, on the full amount, at completion. Idempotent.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "050"
down_revision = "049"
branch_labels = None
depends_on = None

_RECEIPT_STATUS = postgresql.ENUM(
    "PENDING", "APPROVED", "REJECTED",
    name="payoutreceiptstatus",
    create_type=False,
)


def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _RECEIPT_STATUS.create(bind=bind, checkfirst=True)

    if not _has_table(inspector, "payout_receipts"):
        op.create_table(
            "payout_receipts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("payout_id", sa.Integer(),
                      sa.ForeignKey("payouts.id", ondelete="CASCADE"), nullable=False, index=True),
            sa.Column("trader_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("amount", sa.Numeric(15, 4), nullable=False),
            sa.Column("file", sa.String(length=255), nullable=False),
            sa.Column("status", _RECEIPT_STATUS, nullable=False, server_default="PENDING", index=True),
            sa.Column("rejection_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("moderated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("moderated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS payout_receipts")
    bind = op.get_bind()
    _RECEIPT_STATUS.drop(bind=bind, checkfirst=True)
