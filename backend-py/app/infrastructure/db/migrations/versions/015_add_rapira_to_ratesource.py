"""add RAPIRA value to ratesource enum

Revision ID: 015
Revises: 014

Made idempotent for re-runs on partially-applied schemas.
"""
from alembic import op

from app.infrastructure.db.migrations._helpers import (
    enum_exists,
    has_enum_value,
)

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if enum_exists(bind, "ratesource") and not has_enum_value(
        bind, "ratesource", "RAPIRA"
    ):
        # ALTER TYPE ... ADD VALUE must run outside a transaction block.
        # Alembic runs migrations in a transaction by default; commit the
        # current one, execute the ALTER, then reopen a transaction for any
        # following ops.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE ratesource ADD VALUE IF NOT EXISTS 'RAPIRA'")


def downgrade() -> None:
    # PostgreSQL does not support removing a value from an enum type.
    # Downgrade is a no-op to keep the migration reversible in the alembic
    # sense without silently corrupting data.
    pass
