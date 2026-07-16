"""Add UPPERCASE labels to ledgerreferencetype for receipt-check entries.

Revision ID: 031
Revises: 030
Create Date: 2026-05-11

Stays at 031 (its original slot). The two-head conflict with
``030_cascade_rate_source`` was resolved by moving that branch to the
tail of the chain (now 032), leaving the receipt-check pair untouched
where DBs already recorded them.

Background: migration 001 provisioned the schema via
`Base.metadata.create_all`, which uses SQLAlchemy's default Enum behavior —
storing Python member names (UPPERCASE) as Postgres enum labels. The
receipt-check migration mistakenly added lowercase variants
('receipt_check', 'receipt_check_refund'). The ORM emits the UPPER form
when inserting, so inserts blew up with InvalidTextRepresentationError.

This migration adds the correct UPPERCASE labels. Idempotent — safe to
re-run; the leftover lowercase labels stay in the type (harmless, never
referenced by the ORM) because Postgres does not allow removing enum
values without recreating the type.
"""
from alembic import op
import sqlalchemy as sa


revision = "031"
down_revision = "030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = {
        r[0]
        for r in bind.execute(
            sa.text(
                """
                SELECT e.enumlabel
                FROM pg_type t
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE t.typname = 'ledgerreferencetype'
                """
            )
        ).all()
    }

    with op.get_context().autocommit_block():
        for val in ("RECEIPT_CHECK", "RECEIPT_CHECK_REFUND"):
            if val not in existing:
                op.execute(
                    sa.text(
                        f"ALTER TYPE ledgerreferencetype ADD VALUE IF NOT EXISTS '{val}'"
                    )
                )


def downgrade() -> None:
    # Removing enum values requires recreating the type and rewriting all
    # rows that reference it — intentionally a no-op so downgrade stays
    # safe. The extra labels are unused after rollback.
    pass
