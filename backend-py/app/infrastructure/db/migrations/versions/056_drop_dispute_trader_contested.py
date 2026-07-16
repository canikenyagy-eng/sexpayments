"""drop dead disputes.trader_contested_at column

Revision ID: 056
Revises: 055

The column (added in 038) backed the trader "contest → escalate to admin" flow,
which has been replaced by the trader's self-service decision (accept → SUCCESS /
reject → FAILED / request video|pdf → stays OPEN). The trader no longer escalates
without deciding, so the timestamp is never written. Removing the dead field; the
trader's action is still recorded in the audit log. Idempotent for re-runs on
partially-applied schemas.
"""
import sqlalchemy as sa
from alembic import op

from app.infrastructure.db.migrations._helpers import has_column

revision = "056"
down_revision = "055"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "disputes", "trader_contested_at"):
        op.drop_column("disputes", "trader_contested_at")


def downgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "disputes", "trader_contested_at"):
        op.add_column(
            "disputes",
            sa.Column("trader_contested_at", sa.DateTime(timezone=True), nullable=True),
        )
