"""rename requests_usdt to requests_rub in stats_snapshots

Revision ID: 005
Revises: 004

Made idempotent for re-runs on partially-applied schemas.
"""
from alembic import op

from app.infrastructure.db.migrations._helpers import has_column

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "stats_snapshots", "requests_usdt") and not has_column(
        bind, "stats_snapshots", "requests_rub"
    ):
        op.alter_column(
            "stats_snapshots",
            "requests_usdt",
            new_column_name="requests_rub",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "stats_snapshots", "requests_rub") and not has_column(
        bind, "stats_snapshots", "requests_usdt"
    ):
        op.alter_column(
            "stats_snapshots",
            "requests_rub",
            new_column_name="requests_usdt",
        )
