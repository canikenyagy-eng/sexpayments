"""teamlead payout reward % (teamlead_links.payout_fee_percent)

Revision ID: 048
Revises: 047
Create Date: 2026-06-15

Teamlead rewards now apply to payouts too, configured INDEPENDENTLY of the payin
rate: a teamlead linked to a merchant/trader can earn a different % (or nothing,
the default 0) on payouts vs orders. Mirrors how merchant/trader payout commission
is a separate field from the payin fee.

Idempotent + safe: NOT NULL with server_default 0, so existing links keep payin
behaviour unchanged and simply earn 0 on payouts until configured.
"""
from alembic import op

revision = "048"
down_revision = "047"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE teamlead_links "
        "ADD COLUMN IF NOT EXISTS payout_fee_percent NUMERIC(5, 2) NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE teamlead_links DROP COLUMN IF EXISTS payout_fee_percent")
