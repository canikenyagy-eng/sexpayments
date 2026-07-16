"""add_code_to_payment_options

Revision ID: 020
Revises: 019
Create Date: 2026-04-28

Made idempotent for re-runs on partially-applied schemas.
"""
from alembic import op
import sqlalchemy as sa

from app.infrastructure.db.migrations._helpers import (
    has_column,
    has_unique_constraint,
)


revision = "020"
down_revision = "019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # 1. Add 'code' column as nullable
    if not has_column(bind, "payment_options", "code"):
        op.add_column(
            "payment_options",
            sa.Column("code", sa.String(length=50), nullable=True),
        )

    # 2. Populate 'code' for existing rows to ensure no nulls and uniqueness
    op.execute("UPDATE payment_options SET code = 'opt_' || id::text WHERE code IS NULL")

    # 3. Alter column to be NOT NULL (no-op if already NOT NULL)
    if has_column(bind, "payment_options", "code"):
        op.alter_column("payment_options", "code", nullable=False)

    # 4. Add unique constraint
    if not has_unique_constraint(
        bind, "payment_options", "uq_payment_options_code"
    ):
        op.create_unique_constraint(
            "uq_payment_options_code", "payment_options", ["code"]
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_unique_constraint(
        bind, "payment_options", "uq_payment_options_code"
    ):
        op.drop_constraint(
            "uq_payment_options_code", "payment_options", type_="unique"
        )
    if has_column(bind, "payment_options", "code"):
        op.drop_column("payment_options", "code")
