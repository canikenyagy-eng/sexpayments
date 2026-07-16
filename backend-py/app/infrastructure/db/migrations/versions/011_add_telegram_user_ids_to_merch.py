"""add telegram_user_ids to merchants

Revision ID: 011
Revises: 010

Made idempotent for re-runs on partially-applied schemas.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.infrastructure.db.migrations._helpers import has_column

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "merchants", "telegram_user_ids"):
        op.add_column(
            "merchants",
            sa.Column(
                "telegram_user_ids",
                postgresql.JSONB(),
                nullable=False,
                server_default="[]",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "merchants", "telegram_user_ids"):
        op.drop_column("merchants", "telegram_user_ids")
