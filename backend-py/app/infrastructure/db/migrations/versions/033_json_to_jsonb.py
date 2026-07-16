"""convert heavy log/audit JSON columns to JSONB

Revision ID: 033
Revises: 032
Create Date: 2026-05-16

JSONB stores binary; JSON stores text and reparses on every read. For the
append-only logging tables this is a measurable cost — both on write
(slightly less, because the binary form is smaller after TOAST) and on
read (no reparse, GIN-indexable). The ``stats`` repository already pays
this cost at query time via ``cast(... AS JSONB)``; native JSONB removes
that cast.

Each ``ALTER COLUMN ... TYPE JSONB`` rewrites the entire table under an
ACCESS_EXCLUSIVE lock — so this migration must run in a maintenance
window. Tables are processed smallest-first so a partial run still makes
progress on the easy ones.

Idempotent: checks the current column type via ``information_schema`` and
skips columns already on JSONB.
"""
from alembic import op
import sqlalchemy as sa


revision = "033"
down_revision = "032"
branch_labels = None
depends_on = None


# (table, column) pairs to convert. Ordered smallest-first so the long
# table rewrites happen at the end of the maintenance window.
_TARGETS: list[tuple[str, str]] = [
    # outbox — usually small, drained by workers
    ("outbox_events", "payload"),
    # audit_logs — write-on-admin-action, moderate
    ("audit_logs", "old_values"),
    ("audit_logs", "new_values"),
    # callbacks — one row per webhook attempt
    ("callback_attempts", "request_headers"),
    ("callback_attempts", "request_payload"),
    ("callback_attempts", "response_headers"),
    # order_creation_snapshots — one row per payin
    ("order_creation_snapshots", "request_data"),
    ("order_creation_snapshots", "merchant_snapshot"),
    ("order_creation_snapshots", "rate_snapshot"),
    ("order_creation_snapshots", "traders_snapshot"),
    ("order_creation_snapshots", "candidates"),
    ("order_creation_snapshots", "result"),
    # merchant_api_logs — biggest table, headers only (bodies are TEXT)
    ("merchant_api_logs", "request_headers"),
    ("merchant_api_logs", "response_headers"),
]


def _column_udt(bind, table: str, column: str) -> str | None:
    """Return the udt_name (e.g. 'json' / 'jsonb') for table.column, or None."""
    row = bind.execute(
        sa.text(
            """
            SELECT udt_name FROM information_schema.columns
            WHERE table_name = :t AND column_name = :c
            """
        ),
        {"t": table, "c": column},
    ).first()
    return row[0] if row else None


def _convert(bind, table: str, column: str, target: str) -> None:
    """Idempotent ALTER COLUMN ... TYPE between json/jsonb."""
    current = _column_udt(bind, table, column)
    if current is None:
        # column missing (table never created on this env) — skip
        return
    if current == target:
        return
    op.execute(
        sa.text(
            f'ALTER TABLE "{table}" '
            f'ALTER COLUMN "{column}" TYPE {target.upper()} '
            f'USING "{column}"::{target}'
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    for table, column in _TARGETS:
        _convert(bind, table, column, "jsonb")


def downgrade() -> None:
    bind = op.get_bind()
    # reverse order so dependents come first
    for table, column in reversed(_TARGETS):
        _convert(bind, table, column, "json")
