"""add_payment_option_to_requisites

Revision ID: 021
Revises: 020
Create Date: 2026-04-28

Made idempotent for re-runs on partially-applied schemas.
"""
from alembic import op
import sqlalchemy as sa

from app.infrastructure.db.migrations._helpers import (
    has_column,
    has_foreign_key,
    has_index,
)


revision = "021"
down_revision = "020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    if not has_column(bind, "requisites", "payment_option_id"):
        op.add_column(
            "requisites",
            sa.Column("payment_option_id", sa.Integer(), nullable=True),
        )

    if not has_foreign_key(
        bind, "requisites", "fk_requisites_payment_option_id"
    ):
        op.create_foreign_key(
            "fk_requisites_payment_option_id",
            "requisites",
            "payment_options",
            ["payment_option_id"],
            ["id"],
            ondelete="SET NULL",
        )

    if not has_index(bind, "requisites", "ix_requisites_payment_option_id"):
        op.create_index(
            "ix_requisites_payment_option_id",
            "requisites",
            ["payment_option_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_index(bind, "requisites", "ix_requisites_payment_option_id"):
        op.drop_index("ix_requisites_payment_option_id", table_name="requisites")
    if has_foreign_key(
        bind, "requisites", "fk_requisites_payment_option_id"
    ):
        op.drop_constraint(
            "fk_requisites_payment_option_id", "requisites", type_="foreignkey"
        )
    if has_column(bind, "requisites", "payment_option_id"):
        op.drop_column("requisites", "payment_option_id")
