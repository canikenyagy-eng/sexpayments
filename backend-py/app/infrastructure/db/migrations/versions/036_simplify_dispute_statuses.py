"""simplify dispute statuses: drop in_progress / in_review (remap to OPEN)

Revision ID: 036
Revises: 035
Create Date: 2026-06-04

The dispute lifecycle is simplified to OPEN -> RESOLVED / REJECTED. The
intermediate ``in_progress`` / ``in_review`` statuses are removed from the
application (``in_review`` was never assigned; ``in_progress`` may exist on old
rows). Remap any such rows back to OPEN so they load under the reduced Python
enum.

The Postgres enum TYPE keeps the now-unused labels — Postgres can't DROP an enum
value without recreating the type, and leaving them is harmless (nothing writes
them anymore). Labels are uppercase member names (SQLAlchemy ``Enum`` default,
same as ``orders.status``).

Idempotent: re-running simply updates 0 rows.
"""
from alembic import op

revision = "036"
down_revision = "035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Case-robust: resolve the actual ``open`` enum label (uppercase member name
    # under SQLAlchemy's default, but don't assume) and match the intermediate
    # statuses case-insensitively.
    op.execute(
        """
        UPDATE disputes
        SET status = (
            SELECT e.enumlabel
            FROM pg_enum e JOIN pg_type t ON e.enumtypid = t.oid
            WHERE t.typname = 'disputestatus' AND lower(e.enumlabel) = 'open'
            LIMIT 1
        )::disputestatus
        WHERE lower(status::text) IN ('in_progress', 'in_review')
        """
    )


def downgrade() -> None:
    # Irreversible — the intermediate statuses no longer exist in the app.
    pass
