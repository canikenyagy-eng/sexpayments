"""add support user role

Revision ID: 072
Revises: 071
Create Date: 2026-07-19

Adds the limited web-support role used by the receipt moderation workspace.
SQLAlchemy persists PEP-435 enum member names for ``UserRole`` (``ADMIN``,
``TRADER``...), so Postgres needs the uppercase ``SUPPORT`` label.
"""
import sqlalchemy as sa
from alembic import op

revision = "072"
down_revision = "071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(sa.text("ALTER TYPE userrole ADD VALUE IF NOT EXISTS 'SUPPORT'"))


def downgrade() -> None:
    # Removing a Postgres enum value requires recreating the type and rewriting
    # every referencing column. Keep downgrade safe; the extra label is inert.
    pass
