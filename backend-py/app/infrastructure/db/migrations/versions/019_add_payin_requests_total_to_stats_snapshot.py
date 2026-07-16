"""add_payin_requests_total_to_stats_snapshot

Revision ID: 019
Revises: 018
Create Date: 2026-04-26

Made idempotent for re-runs on partially-applied schemas.
"""
from alembic import op
import sqlalchemy as sa

from app.infrastructure.db.migrations._helpers import has_column

revision = "019"
down_revision = "018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "stats_snapshots", "payin_requests_total"):
        op.add_column(
            "stats_snapshots",
            sa.Column(
                "payin_requests_total",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "stats_snapshots", "payin_requests_total"):
        op.drop_column("stats_snapshots", "payin_requests_total")
