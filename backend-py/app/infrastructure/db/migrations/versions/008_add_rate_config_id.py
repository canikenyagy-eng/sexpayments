"""add rate_config_id to merchants

Revision ID: 008
Revises: 007

Made idempotent for re-runs on partially-applied schemas.
"""
from alembic import op
import sqlalchemy as sa

from app.infrastructure.db.migrations._helpers import (
    has_column,
    has_foreign_key,
)

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    if not has_column(bind, "merchants", "rate_config_id"):
        op.add_column(
            "merchants",
            sa.Column("rate_config_id", sa.Integer(), nullable=True),
        )

    if not has_foreign_key(bind, "merchants", "fk_merchants_rate_config_id"):
        op.create_foreign_key(
            "fk_merchants_rate_config_id",
            "merchants",
            "rate_configs",
            ["rate_config_id"],
            ["id"],
        )


def downgrade() -> None:
    bind = op.get_bind()

    if has_foreign_key(bind, "merchants", "fk_merchants_rate_config_id"):
        op.drop_constraint(
            "fk_merchants_rate_config_id", "merchants", type_="foreignkey"
        )
    if has_column(bind, "merchants", "rate_config_id"):
        op.drop_column("merchants", "rate_config_id")
