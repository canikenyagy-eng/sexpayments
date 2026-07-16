"""clients.public_id (internal client UUID) + merchants.unique_clients_enabled

Adds:
  * ``clients.public_id`` — our internal, merchant-opaque client id. A volatile
    ``gen_random_uuid()`` server default means the ``ADD COLUMN`` rewrite assigns
    a DISTINCT uuid to every existing row (backfill for free), and the default
    stays so the raw materialise / blocked-attempt upserts (which don't list the
    column) fill it on new inserts too.
  * ``merchants.unique_clients_enabled`` — per-merchant toggle (default OFF).
    When ON the admin order modal shows a «Client» block for that merchant's
    orders (and the API serves it); OFF hides it and never sends it.

Revision ID: 063
Revises: 062
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from app.infrastructure.db.migrations._helpers import has_column, has_index

revision = "063"
down_revision = "062"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # gen_random_uuid() is core in PostgreSQL 13+, but ensure pgcrypto so older
    # servers (and the default expression) resolve it.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    if not has_column(bind, "clients", "public_id"):
        op.add_column(
            "clients",
            sa.Column(
                "public_id",
                postgresql.UUID(as_uuid=True),
                nullable=False,
                server_default=sa.text("gen_random_uuid()"),
            ),
        )
    if not has_index(bind, "clients", "ix_clients_public_id"):
        op.create_index("ix_clients_public_id", "clients", ["public_id"], unique=True)

    if not has_column(bind, "merchants", "unique_clients_enabled"):
        op.add_column(
            "merchants",
            sa.Column(
                "unique_clients_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "merchants", "unique_clients_enabled"):
        op.drop_column("merchants", "unique_clients_enabled")
    if has_index(bind, "clients", "ix_clients_public_id"):
        op.drop_index("ix_clients_public_id", table_name="clients")
    if has_column(bind, "clients", "public_id"):
        op.drop_column("clients", "public_id")
