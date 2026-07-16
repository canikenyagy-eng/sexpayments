"""merchants.dispute_id_mask (token-position mask for appeal-id extraction)

Revision ID: 044
Revises: 043
Create Date: 2026-06-07

Different merchants format their dispute/appeal messages differently — our order
id is often NOT the first token (their own id is). This per-merchant mask tells
the merchant-dispute-bot intake which token carries OUR id:
    "N" / "word:N" → the N-th whitespace token (1-based)
    "uuid:N"       → the N-th UUID-shaped token
The picked token is resolved against our DB; NULL — no mask (scan all tokens).

Idempotent: ADD/DROP COLUMN IF [NOT] EXISTS.
"""
from alembic import op

revision = "044"
down_revision = "043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE merchants "
        "ADD COLUMN IF NOT EXISTS dispute_id_mask VARCHAR(50)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE merchants DROP COLUMN IF EXISTS dispute_id_mask"
    )
