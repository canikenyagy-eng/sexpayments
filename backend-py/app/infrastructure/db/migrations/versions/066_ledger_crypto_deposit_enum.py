"""add 'CRYPTO_DEPOSIT' to the ledgerreferencetype enum

Revision ID: 066
Revises: 065
Create Date: 2026-07-05

The admin "top-up by hash" flow writes a ledger entry with
``LedgerReferenceType.CRYPTO_DEPOSIT``. ``ledger_entries.reference_type`` is a
native Postgres ENUM storing the Python member NAMES (uppercase) — see
``031_receipt_check_uppercase`` / ``053_doliv_ledger_enum``. Adding the value to
the Python enum is not enough: the Postgres type needs the label too, or INSERTs
fail with "invalid input value for enum ledgerreferencetype: 'CRYPTO_DEPOSIT'".
This adds the label idempotently.

(No-op on SQLite — the test engine stores enums as TEXT.)
"""
import sqlalchemy as sa
from alembic import op

revision = "066"
down_revision = "065"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # ALTER TYPE ... ADD VALUE cannot run inside a transaction block — use the
    # autocommit escape hatch, same as 031 / 053.
    with op.get_context().autocommit_block():
        op.execute(
            sa.text("ALTER TYPE ledgerreferencetype ADD VALUE IF NOT EXISTS 'CRYPTO_DEPOSIT'")
        )


def downgrade() -> None:
    # Removing an enum value requires recreating the type and rewriting every
    # referencing row — intentionally a no-op so downgrade stays safe. The extra
    # label is harmless when unused.
    pass
