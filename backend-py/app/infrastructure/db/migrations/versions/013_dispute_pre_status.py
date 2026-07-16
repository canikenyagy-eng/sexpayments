"""add pre_dispute_order_status to disputes

Revision ID: 013
Revises: 012

Made idempotent for re-runs on partially-applied schemas.
"""
import sqlalchemy as sa
from alembic import op

from app.infrastructure.db.migrations._helpers import has_column

revision = "013"
down_revision = "012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "disputes", "pre_dispute_order_status"):
        op.add_column(
            "disputes",
            sa.Column("pre_dispute_order_status", sa.String(length=50), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "disputes", "pre_dispute_order_status"):
        op.drop_column("disputes", "pre_dispute_order_status")
