"""add 'DOLIV' to the ledgerreferencetype enum

Revision ID: 053
Revises: 052
Create Date: 2026-06-19

The долив money flow writes ledger entries with
``LedgerReferenceType.DOLIV``. The ``ledger_entries.reference_type`` column is a
native Postgres ENUM storing the Python member NAMES (uppercase) — see
``031_receipt_check_uppercase``. Adding the value to the Python enum is not
enough: the Postgres type needs the label too, or INSERTs fail with
"invalid input value for enum ledgerreferencetype: 'DOLIV'" (which surfaced as a
500 on the first долив create). This adds the label idempotently.

(No-op on SQLite — the test engine stores enums as TEXT.)
"""
import sqlalchemy as sa
from alembic import op

revision = "053"
down_revision = "052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block — use the
    # autocommit escape hatch, same as 031.
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("ALTER TYPE ledgerreferencetype ADD VALUE IF NOT EXISTS 'DOLIV'")
        )


def downgrade() -> None:
    # Removing an enum value requires recreating the type and rewriting every
    # referencing row — intentionally a no-op so downgrade stays safe. The extra
    # label is harmless when unused.
    pass
