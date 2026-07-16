"""premoderation reminder tracking (receipt_moderations.reminded_at)

Revision ID: 051
Revises: 050
Create Date: 2026-06-18

Adds ``reminded_at`` to ``receipt_moderations`` — the timestamp of the LAST
"check is waiting" reminder the support-bot sent for an undecided check. The
periodic reminder task re-pings when ``decision IS NULL`` and the last reminder
(or the original card, when NULL) is older than the configured
``premoderation_reminder_minutes``. A partial index keeps that recurring scan
cheap.

Idempotent + safe: nullable column, no backfill needed (NULL = never reminded).
"""
from alembic import op

revision = "051"
down_revision = "050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE receipt_moderations "
        "ADD COLUMN IF NOT EXISTS reminded_at TIMESTAMPTZ NULL"
    )
    # Recurring reminder scan only ever looks at still-undecided rows.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_receipt_moderations_pending_reminder "
        "ON receipt_moderations (created_at) "
        "WHERE decision IS NULL AND message_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_receipt_moderations_pending_reminder")
    op.execute("ALTER TABLE receipt_moderations DROP COLUMN IF EXISTS reminded_at")
