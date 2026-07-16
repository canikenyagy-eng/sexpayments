"""долив (requisite refill) — payouts.is_doliv + funding/refill columns

Revision ID: 052
Revises: 051
Create Date: 2026-06-19

A долив is a payout a trader requests against their OWN under-used payin
requisite, funded by the requesting trader and fulfilled by a "доливщик" from
the platform-settings executor list. It lives in the ``payouts`` table flagged
``is_doliv`` so it surfaces in the existing payout pool / "my payouts" views,
while a dedicated DolivService owns its money flow.

Adds the doliv columns and relaxes ``payout_terminal_id`` to NULLABLE (доливы
have no terminal — they are trader-funded). Idempotent + additive.
"""
from alembic import op

revision = "052"
down_revision = "051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE payouts ALTER COLUMN payout_terminal_id DROP NOT NULL")
    op.execute(
        "ALTER TABLE payouts "
        "ADD COLUMN IF NOT EXISTS is_doliv BOOLEAN NOT NULL DEFAULT false"
    )
    op.execute("ALTER TABLE payouts ADD COLUMN IF NOT EXISTS requester_trader_id INTEGER NULL")
    op.execute("ALTER TABLE payouts ADD COLUMN IF NOT EXISTS refill_order_id INTEGER NULL")
    op.execute("ALTER TABLE payouts ADD COLUMN IF NOT EXISTS refill_requisite_id INTEGER NULL")
    op.execute("ALTER TABLE payouts ADD COLUMN IF NOT EXISTS doliv_price_usdt NUMERIC(15, 4) NULL")
    # The doliv pool query filters by (is_doliv, status); a partial index keeps it cheap.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payouts_doliv_pool "
        "ON payouts (status) WHERE is_doliv = true"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payouts_requester_trader_id "
        "ON payouts (requester_trader_id) WHERE requester_trader_id IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_payouts_requester_trader_id")
    op.execute("DROP INDEX IF EXISTS ix_payouts_doliv_pool")
    op.execute("ALTER TABLE payouts DROP COLUMN IF EXISTS doliv_price_usdt")
    op.execute("ALTER TABLE payouts DROP COLUMN IF EXISTS refill_requisite_id")
    op.execute("ALTER TABLE payouts DROP COLUMN IF EXISTS refill_order_id")
    op.execute("ALTER TABLE payouts DROP COLUMN IF EXISTS requester_trader_id")
    op.execute("ALTER TABLE payouts DROP COLUMN IF EXISTS is_doliv")
    # Leaves payout_terminal_id nullable on downgrade (safe; pre-долив rows always set it).
