"""orders financial snapshot (teamlead_reward_usdt, platform_profit_usdt, financials)

Revision ID: 047
Revises: 046
Create Date: 2026-06-14

Denormalized financial projection on the order (the ledger stays the single
source of truth). OrderService maintains these on every status / amount
transition:

  * ``teamlead_reward_usdt`` — Σ of teamlead rewards paid for the order
    (finalized at SUCCESS, zeroed when settlement is reversed).
  * ``platform_profit_usdt`` — platform net margin = fee − trader_fee −
    teamlead_reward.
  * ``financials`` (JSONB) — full breakdown incl. the per-teamlead reward list,
    for one-shot reads / reporting.

Nullable, no backfill: existing rows stay NULL until their next transition
re-projects them (the projection is lazy, never authoritative). Idempotent:
ADD/DROP COLUMN IF [NOT] EXISTS.
"""
from alembic import op

revision = "047"
down_revision = "046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE orders "
        "ADD COLUMN IF NOT EXISTS teamlead_reward_usdt NUMERIC(15, 4)"
    )
    op.execute(
        "ALTER TABLE orders "
        "ADD COLUMN IF NOT EXISTS platform_profit_usdt NUMERIC(15, 4)"
    )
    op.execute(
        "ALTER TABLE orders "
        "ADD COLUMN IF NOT EXISTS financials JSONB"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS financials")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS platform_profit_usdt")
    op.execute("ALTER TABLE orders DROP COLUMN IF EXISTS teamlead_reward_usdt")
