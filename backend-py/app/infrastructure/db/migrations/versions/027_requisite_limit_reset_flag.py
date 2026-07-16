"""requisite_limits: optional auto-reset flags + last reset timestamps

Revision ID: 027
Revises: 026
Create Date: 2026-05-07

Adds four optional columns to ``requisite_limits``:

  * ``daily_reset_enabled``    – BOOLEAN NOT NULL DEFAULT FALSE
  * ``monthly_reset_enabled``  – BOOLEAN NOT NULL DEFAULT FALSE
  * ``last_daily_reset_at``    – TIMESTAMPTZ NULL
  * ``last_monthly_reset_at``  – TIMESTAMPTZ NULL

By default the flags are FALSE — turnover counters never reset (current
behaviour). When a flag is enabled, the periodic ``reset_requisite_limits``
celery beat task zeroes the matching counter at each day/month boundary.

Idempotent: skips columns that already exist.
"""
from alembic import op
import sqlalchemy as sa


revision = "027"
down_revision = "026"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "requisite_limits", "daily_reset_enabled"):
        op.add_column(
            "requisite_limits",
            sa.Column(
                "daily_reset_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if not _has_column(inspector, "requisite_limits", "monthly_reset_enabled"):
        op.add_column(
            "requisite_limits",
            sa.Column(
                "monthly_reset_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if not _has_column(inspector, "requisite_limits", "last_daily_reset_at"):
        op.add_column(
            "requisite_limits",
            sa.Column("last_daily_reset_at", sa.DateTime(timezone=True), nullable=True),
        )
    if not _has_column(inspector, "requisite_limits", "last_monthly_reset_at"):
        op.add_column(
            "requisite_limits",
            sa.Column("last_monthly_reset_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "requisite_limits", "last_monthly_reset_at"):
        op.drop_column("requisite_limits", "last_monthly_reset_at")
    if _has_column(inspector, "requisite_limits", "last_daily_reset_at"):
        op.drop_column("requisite_limits", "last_daily_reset_at")
    if _has_column(inspector, "requisite_limits", "monthly_reset_enabled"):
        op.drop_column("requisite_limits", "monthly_reset_enabled")
    if _has_column(inspector, "requisite_limits", "daily_reset_enabled"):
        op.drop_column("requisite_limits", "daily_reset_enabled")
