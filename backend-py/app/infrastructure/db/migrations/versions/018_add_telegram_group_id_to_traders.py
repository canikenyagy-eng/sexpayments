"""add_telegram_group_id_to_traders

Revision ID: 018
Revises: 017
Create Date: 2026-04-21

Idempotent: on freshly-created databases the column is already created
by 002_add_missing_tables (which materialises `traders` with the modern
column set). On upgraded databases the column is missing and we add it
here. The `_has_column` guard keeps both paths green.
"""
from alembic import op
import sqlalchemy as sa


revision = "018"
down_revision = "017"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not _has_column(inspector, "traders", "telegram_group_id"):
        op.add_column(
            "traders",
            sa.Column("telegram_group_id", sa.BigInteger(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if _has_column(inspector, "traders", "telegram_group_id"):
        op.drop_column("traders", "telegram_group_id")
