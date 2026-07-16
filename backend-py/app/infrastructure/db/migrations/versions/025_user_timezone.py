"""users.timezone

Revision ID: 025
Revises: 024
Create Date: 2026-05-05

Adds a per-user IANA timezone preference (e.g. "Europe/Moscow"). NULL means
"use the browser timezone" (frontend default).

Idempotent: migration 001 uses ``Base.metadata.create_all(checkfirst=True)``
so the column is present on a fresh database via ORM model. Older databases
need this migration to add it; we detect existing column to be safe.
"""
from alembic import op
import sqlalchemy as sa


revision = "025"
down_revision = "024"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "users", "timezone"):
        op.add_column(
            "users",
            sa.Column("timezone", sa.String(length=64), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "users", "timezone"):
        op.drop_column("users", "timezone")
