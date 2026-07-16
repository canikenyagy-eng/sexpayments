"""clients.blocked_attempts — count of withheld requisites for a blocked client

Revision ID: 059
Revises: 058

Adds ``blocked_attempts``: how many times we refused to issue a requisite because
the client was blocked (the ban hot-path rejection). It is counted on the
already-rejected branch via a Redis ``HINCRBY`` (never on the success path) and
folded into this column off the hot path by ``refresh_clients_task`` — order
creation never writes here. Idempotent for re-runs on partially-applied schemas.
"""
import sqlalchemy as sa
from alembic import op

from app.infrastructure.db.migrations._helpers import has_column

revision = "059"
down_revision = "058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "clients", "blocked_attempts"):
        op.add_column(
            "clients",
            sa.Column("blocked_attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "clients", "blocked_attempts"):
        op.drop_column("clients", "blocked_attempts")
