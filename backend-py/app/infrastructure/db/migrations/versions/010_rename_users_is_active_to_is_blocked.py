"""rename users.is_active to is_blocked

Revision ID: 010
Revises: 009

Made idempotent for re-runs on partially-applied schemas.
"""
from alembic import op

from app.infrastructure.db.migrations._helpers import has_column

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "users", "is_active") and not has_column(
        bind, "users", "is_blocked"
    ):
        op.alter_column("users", "is_active", new_column_name="is_blocked")
        # Flip semantics: is_active=True (not blocked) -> is_blocked=False
        op.execute("UPDATE users SET is_blocked = NOT is_blocked")


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "users", "is_blocked") and not has_column(
        bind, "users", "is_active"
    ):
        op.execute("UPDATE users SET is_blocked = NOT is_blocked")
        op.alter_column("users", "is_blocked", new_column_name="is_active")
