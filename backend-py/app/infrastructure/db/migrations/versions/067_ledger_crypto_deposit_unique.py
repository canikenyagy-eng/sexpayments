"""partial unique index on ledger_entries for CRYPTO_DEPOSIT idempotency

Revision ID: 067
Revises: 066
Create Date: 2026-07-05

The admin "top-up by hash" flow stores the TRC20 tx hash in
``ledger_entries.reference_id`` under ``reference_type = 'CRYPTO_DEPOSIT'``. A
partial UNIQUE index makes that hash idempotent at the database level — the same
hash can never credit a trader twice, even under two concurrent confirms. Scoped
to crypto deposits so other reference types (which legitimately reuse ids such as
order / withdrawal ids) are unaffected.

Idempotent for re-runs on partially-applied schemas.
"""
import sqlalchemy as sa
from alembic import op

from app.infrastructure.db.migrations._helpers import has_index

revision = "067"
down_revision = "066"
branch_labels = None
depends_on = None

_INDEX = "uq_ledger_crypto_deposit_ref"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if not has_index(bind, "ledger_entries", _INDEX):
        op.create_index(
            _INDEX,
            "ledger_entries",
            ["reference_id"],
            unique=True,
            postgresql_where=sa.text("reference_type = 'CRYPTO_DEPOSIT'"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    if has_index(bind, "ledger_entries", _INDEX):
        op.drop_index(_INDEX, table_name="ledger_entries")
