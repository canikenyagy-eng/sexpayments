"""add_24h_stats_columns

Revision ID: 022
Revises: 021
Create Date: 2026-04-30

Made idempotent for re-runs on partially-applied schemas.
"""
from alembic import op
import sqlalchemy as sa

from app.infrastructure.db.migrations._helpers import has_column


revision = "022"
down_revision = "021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "stats_snapshots", "payin_requests_24h"):
        op.add_column(
            "stats_snapshots",
            sa.Column(
                "payin_requests_24h",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
        )
    if not has_column(bind, "stats_snapshots", "requests_rub_24h"):
        op.add_column(
            "stats_snapshots",
            sa.Column(
                "requests_rub_24h",
                sa.Numeric(precision=20, scale=4),
                nullable=False,
                server_default="0",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "stats_snapshots", "payin_requests_24h"):
        op.drop_column("stats_snapshots", "payin_requests_24h")
    if has_column(bind, "stats_snapshots", "requests_rub_24h"):
        op.drop_column("stats_snapshots", "requests_rub_24h")
