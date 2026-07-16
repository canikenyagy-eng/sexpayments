"""requisite_limits: collapse daily+monthly reset flags into a single reset_enabled

Revision ID: 028
Revises: 027
Create Date: 2026-05-07

Migration 027 added separate ``daily_reset_enabled`` and ``monthly_reset_enabled``
flags. UX feedback: a single toggle is enough — when it's on, both counters
reset at their respective boundaries; when it's off, the reset is disabled
entirely and the counters act as a lifetime / "общий" limit.

This migration:
  * adds ``reset_enabled`` BOOLEAN NOT NULL DEFAULT FALSE
  * adds ``last_reset_at`` TIMESTAMPTZ NULL
  * back-fills:
      reset_enabled = (daily_reset_enabled OR monthly_reset_enabled)
      last_reset_at = COALESCE(last_daily_reset_at, last_monthly_reset_at)
  * drops the four 027 columns

Idempotent: skipped operations are no-ops if columns are already the new shape.
"""
from alembic import op
import sqlalchemy as sa


revision = "028"
down_revision = "027"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "requisite_limits", "reset_enabled"):
        op.add_column(
            "requisite_limits",
            sa.Column(
                "reset_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
    if not _has_column(inspector, "requisite_limits", "last_reset_at"):
        op.add_column(
            "requisite_limits",
            sa.Column("last_reset_at", sa.DateTime(timezone=True), nullable=True),
        )

    # Back-fill from old columns, if they're still present.
    inspector = sa.inspect(bind)
    has_old_flags = (
        _has_column(inspector, "requisite_limits", "daily_reset_enabled")
        or _has_column(inspector, "requisite_limits", "monthly_reset_enabled")
    )
    has_old_ts = (
        _has_column(inspector, "requisite_limits", "last_daily_reset_at")
        or _has_column(inspector, "requisite_limits", "last_monthly_reset_at")
    )
    if has_old_flags:
        # Coalesce the two booleans into the single new flag.
        flag_parts = []
        if _has_column(inspector, "requisite_limits", "daily_reset_enabled"):
            flag_parts.append("COALESCE(daily_reset_enabled, FALSE)")
        if _has_column(inspector, "requisite_limits", "monthly_reset_enabled"):
            flag_parts.append("COALESCE(monthly_reset_enabled, FALSE)")
        if flag_parts:
            op.execute(
                sa.text(
                    "UPDATE requisite_limits SET reset_enabled = ("
                    + " OR ".join(flag_parts)
                    + ") WHERE reset_enabled IS DISTINCT FROM ("
                    + " OR ".join(flag_parts)
                    + ")"
                )
            )
    if has_old_ts:
        ts_parts = []
        if _has_column(inspector, "requisite_limits", "last_daily_reset_at"):
            ts_parts.append("last_daily_reset_at")
        if _has_column(inspector, "requisite_limits", "last_monthly_reset_at"):
            ts_parts.append("last_monthly_reset_at")
        if ts_parts:
            op.execute(
                sa.text(
                    "UPDATE requisite_limits SET last_reset_at = COALESCE("
                    + ", ".join(ts_parts)
                    + ") WHERE last_reset_at IS NULL"
                )
            )

    # Drop legacy columns. Use DROP IF EXISTS to stay idempotent.
    for col in (
        "daily_reset_enabled",
        "monthly_reset_enabled",
        "last_daily_reset_at",
        "last_monthly_reset_at",
    ):
        if _has_column(inspector, "requisite_limits", col):
            op.execute(sa.text(f'ALTER TABLE requisite_limits DROP COLUMN IF EXISTS "{col}"'))


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Re-create the old shape so 027 still has something to roll back from.
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

    # Restore values from the consolidated columns.
    op.execute(
        sa.text(
            "UPDATE requisite_limits "
            "SET daily_reset_enabled = COALESCE(reset_enabled, FALSE), "
            "    monthly_reset_enabled = COALESCE(reset_enabled, FALSE), "
            "    last_daily_reset_at = last_reset_at, "
            "    last_monthly_reset_at = last_reset_at"
        )
    )

    # Drop the consolidated columns we added in upgrade().
    for col in ("last_reset_at", "reset_enabled"):
        if _has_column(inspector, "requisite_limits", col):
            op.execute(sa.text(f'ALTER TABLE requisite_limits DROP COLUMN IF EXISTS "{col}"'))
