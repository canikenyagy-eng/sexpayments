"""add order_creation_snapshots table

Revision ID: 007
Revises: 006

Made idempotent for re-runs on partially-applied schemas.
"""
from alembic import op
import sqlalchemy as sa

from app.infrastructure.db.migrations._helpers import has_table

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, "order_creation_snapshots"):
        op.create_table(
            "order_creation_snapshots",
            sa.Column("id", sa.Integer(), primary_key=True, index=True),
            sa.Column(
                "api_log_id",
                sa.Integer(),
                sa.ForeignKey("merchant_api_logs.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
                index=True,
            ),
            sa.Column("request_data", sa.JSON(), nullable=False),
            sa.Column("merchant_snapshot", sa.JSON(), nullable=False),
            sa.Column("rate_snapshot", sa.JSON(), nullable=True),
            sa.Column("traders_snapshot", sa.JSON(), nullable=False),
            sa.Column("candidates", sa.JSON(), nullable=False),
            sa.Column("excluded", sa.JSON(), nullable=False),
            sa.Column("result", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_table(bind, "order_creation_snapshots"):
        op.drop_table("order_creation_snapshots")
