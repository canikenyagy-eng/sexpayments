"""add nickname to requisites

Revision ID: 014
Revises: 013

Made idempotent for re-runs on partially-applied schemas.
"""
import sqlalchemy as sa
from alembic import op

from app.infrastructure.db.migrations._helpers import has_column

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "requisites", "nickname"):
        op.add_column(
            "requisites",
            sa.Column("nickname", sa.String(length=100), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "requisites", "nickname"):
        op.drop_column("requisites", "nickname")
