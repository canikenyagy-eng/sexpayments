"""convert all naive DateTime columns to TIMESTAMP WITH TIME ZONE

Revision ID: 026
Revises: 025
Create Date: 2026-05-05

The codebase moved every model timestamp default from ``datetime.utcnow``
(naive) to ``app.common.types.utcnow`` (UTC tz-aware). asyncpg refuses to
INSERT/UPDATE a tz-aware datetime into a ``TIMESTAMP WITHOUT TIME ZONE``
column, so every existing prod/dev DB started returning 500 on any write
to these tables. This migration aligns the DB schema with the model:

    ALTER COLUMN <col> TYPE TIMESTAMPTZ USING <col> AT TIME ZONE 'UTC'

Existing naive values are interpreted as UTC, which matches what the
application has always written.

Idempotent: introspects ``information_schema`` and only alters columns
that are still ``timestamp without time zone``. Down-migration reverses
to naive (``AT TIME ZONE 'UTC'`` back to plain timestamp).
"""
from alembic import op
import sqlalchemy as sa


revision = "026"
down_revision = "025"
branch_labels = None
depends_on = None


# (table, column) pairs to convert. Source of truth: every Column(DateTime, ...)
# that was switched to Column(DateTime(timezone=True), ...) in the corresponding
# models. requisite_limits.updated_at was already tz-aware, so it is omitted.
COLUMNS: list[tuple[str, str]] = [
    ("users", "created_at"),
    ("audit_logs", "created_at"),
    ("merchant_api_logs", "created_at"),
    ("order_creation_snapshots", "created_at"),
    ("callback_attempts", "created_at"),
    ("disputes", "resolved_at"),
    ("disputes", "created_at"),
    ("disputes", "updated_at"),
    ("dispute_comments", "created_at"),
    ("balances", "created_at"),
    ("balances", "updated_at"),
    ("ledger_entries", "created_at"),
    ("withdrawal_requests", "created_at"),
    ("withdrawal_requests", "updated_at"),
    ("withdrawal_requests", "processed_at"),
    ("orders", "receipt_uploaded_at"),
    ("orders", "created_at"),
    ("orders", "updated_at"),
    ("orders", "date_end"),
    ("orders", "confirmed_at"),
    ("orders", "rejected_at"),
    ("order_status_history", "created_at"),
    ("requisites", "status_updated_at"),
    ("requisites", "last_used_at"),
    ("rate_configs", "last_updated_at"),
    ("stats_snapshots", "data_cutoff_at"),
    ("stats_snapshots", "created_at"),
    ("merchant_stats_snapshots", "data_cutoff_at"),
    ("merchant_stats_snapshots", "created_at"),
    ("teamlead_links", "created_at"),
    ("teamlead_links", "updated_at"),
    ("outbox_events", "created_at"),
    ("outbox_events", "processed_at"),
]


def _column_type(bind, table: str, column: str) -> str | None:
    """Return current `data_type` for a column, or None if column/table missing."""
    res = bind.execute(
        sa.text(
            """
            SELECT data_type
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = :table
              AND column_name = :column
            """
        ),
        {"table": table, "column": column},
    ).first()
    return res[0] if res else None


def upgrade() -> None:
    bind = op.get_bind()

    for table, column in COLUMNS:
        current = _column_type(bind, table, column)
        if current is None:
            # Table or column missing on this DB — skip (migration 001 may not
            # have created it, or migration 024 hasn't run yet, etc.).
            continue
        if current == "timestamp with time zone":
            continue  # already converted
        # current is typically "timestamp without time zone"
        op.execute(
            sa.text(
                f'ALTER TABLE "{table}" '
                f'ALTER COLUMN "{column}" TYPE TIMESTAMP WITH TIME ZONE '
                f'USING "{column}" AT TIME ZONE \'UTC\''
            )
        )


def downgrade() -> None:
    bind = op.get_bind()

    for table, column in COLUMNS:
        current = _column_type(bind, table, column)
        if current is None:
            continue
        if current == "timestamp without time zone":
            continue
        op.execute(
            sa.text(
                f'ALTER TABLE "{table}" '
                f'ALTER COLUMN "{column}" TYPE TIMESTAMP WITHOUT TIME ZONE '
                f'USING "{column}" AT TIME ZONE \'UTC\''
            )
        )
