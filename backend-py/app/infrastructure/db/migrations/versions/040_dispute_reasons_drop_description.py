"""new dispute reasons + drop disputes.description

Revision ID: 040
Revises: 039
Create Date: 2026-06-04

Two merchant-facing dispute changes:
  1. Replace the DisputeReason set with: unknown / has_payment / no_payment /
     invalid_sum / invalid_requisites (old: not_paid / fraud / wrong_amount /
     other). The column is a native PG enum ``disputereason`` whose labels are
     the uppercase member NAMES (SQLAlchemy ``Enum`` default, same as
     orders.status / disputestatus). Existing rows are remapped:
       not_paid     -> NO_PAYMENT
       wrong_amount -> INVALID_SUM
       fraud, other -> UNKNOWN
     We recreate the type (rather than ALTER TYPE ... ADD VALUE) so the whole
     migration stays inside one transaction and is version-agnostic.
  2. Drop the free-text ``description`` column — disputes no longer carry it.

``disputereason`` is used only by ``disputes.reason``, so the type swap is safe.
Idempotent-ish: re-running preserves already-migrated (new) labels and guards
with IF EXISTS / IF NOT EXISTS.
"""
from alembic import op

revision = "040"
down_revision = "039"
branch_labels = None
depends_on = None

_NEW_LABELS = "'UNKNOWN','HAS_PAYMENT','NO_PAYMENT','INVALID_SUM','INVALID_REQUISITES'"
_OLD_LABELS = "'NOT_PAID','FRAUD','WRONG_AMOUNT','OTHER'"


def upgrade() -> None:
    # 1. Drop the free-text description column.
    op.execute("ALTER TABLE disputes DROP COLUMN IF EXISTS description")

    # 2. Swap the reason enum. Detach the column to text, remap, recreate the
    #    type with the new labels, re-attach.
    op.execute("ALTER TABLE disputes ALTER COLUMN reason TYPE text USING reason::text")
    op.execute(
        """
        UPDATE disputes SET reason = CASE
            WHEN lower(reason) = 'not_paid'         THEN 'NO_PAYMENT'
            WHEN lower(reason) = 'wrong_amount'     THEN 'INVALID_SUM'
            WHEN lower(reason) IN ('fraud', 'other') THEN 'UNKNOWN'
            WHEN upper(reason) IN ('UNKNOWN','HAS_PAYMENT','NO_PAYMENT','INVALID_SUM','INVALID_REQUISITES')
                THEN upper(reason)
            ELSE 'UNKNOWN'
        END
        """
    )
    op.execute("DROP TYPE IF EXISTS disputereason")
    op.execute(f"CREATE TYPE disputereason AS ENUM ({_NEW_LABELS})")
    op.execute("ALTER TABLE disputes ALTER COLUMN reason TYPE disputereason USING reason::disputereason")


def downgrade() -> None:
    # Best-effort reverse: remap new -> old, restore the old type, re-add a
    # (data-less) description column.
    op.execute("ALTER TABLE disputes ALTER COLUMN reason TYPE text USING reason::text")
    op.execute(
        """
        UPDATE disputes SET reason = CASE
            WHEN upper(reason) = 'NO_PAYMENT'  THEN 'NOT_PAID'
            WHEN upper(reason) = 'INVALID_SUM' THEN 'WRONG_AMOUNT'
            ELSE 'OTHER'
        END
        """
    )
    op.execute("DROP TYPE IF EXISTS disputereason")
    op.execute(f"CREATE TYPE disputereason AS ENUM ({_OLD_LABELS})")
    op.execute("ALTER TABLE disputes ALTER COLUMN reason TYPE disputereason USING reason::disputereason")
    op.execute("ALTER TABLE disputes ADD COLUMN IF NOT EXISTS description text NOT NULL DEFAULT ''")
