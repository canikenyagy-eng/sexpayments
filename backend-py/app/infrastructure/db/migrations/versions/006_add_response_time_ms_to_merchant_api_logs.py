"""add response_time_ms to merchant_api_logs

Revision ID: 006
Revises: 005

Made idempotent for re-runs on partially-applied schemas.
"""
from alembic import op
import sqlalchemy as sa

from app.infrastructure.db.migrations._helpers import has_column

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "merchant_api_logs", "response_time_ms"):
        op.add_column(
            "merchant_api_logs",
            sa.Column("response_time_ms", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "merchant_api_logs", "response_time_ms"):
        op.drop_column("merchant_api_logs", "response_time_ms")
