"""drop dead disputes.pre_dispute_order_status column

Revision ID: 055
Revises: 054

The column (added in 013) was never read: dispute resolution always routes to
SUCCESS (merchant favour) / FAILED (trader favour) regardless of the pre-dispute
status. Removing the dead field; the pre-dispute status is still recorded in the
audit log on open. Idempotent for re-runs on partially-applied schemas.
"""
import sqlalchemy as sa
from alembic import op

from app.infrastructure.db.migrations._helpers import has_column

revision = "055"
down_revision = "054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "disputes", "pre_dispute_order_status"):
        op.drop_column("disputes", "pre_dispute_order_status")


def downgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "disputes", "pre_dispute_order_status"):
        op.add_column(
            "disputes",
            sa.Column("pre_dispute_order_status", sa.String(length=50), nullable=True),
        )
