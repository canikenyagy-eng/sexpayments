"""merchant_stats_snapshots: +requests_rub, +payin_requests_total

Revision ID: 039
Revises: 038
Create Date: 2026-06-04

Per-merchant admin stat cards need parity with the global dashboard's
«Запросов RUB» and «Выдача %». «Выдача %» = orders_total / payin_requests_total,
so we persist the two components that delta-update independently:

  * requests_rub          — Σ amount over the merchant's payin API requests
                            (merchant_api_logs, incl. ones that 404'd before
                            an order was created). Source of truth for the
                            «Запросов RUB» card.
  * payin_requests_total  — COUNT of those same API requests. Denominator
                            for «Выдача %».

Both default 0; existing rows backfill to 0 until the next snapshot refresh
(beat every 600s) recomputes them. Idempotent (IF NOT EXISTS).
"""
from alembic import op

revision = "039"
down_revision = "038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE merchant_stats_snapshots "
        "ADD COLUMN IF NOT EXISTS requests_rub NUMERIC(20, 4) NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE merchant_stats_snapshots "
        "ADD COLUMN IF NOT EXISTS payin_requests_total INTEGER NOT NULL DEFAULT 0"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE merchant_stats_snapshots DROP COLUMN IF EXISTS requests_rub"
    )
    op.execute(
        "ALTER TABLE merchant_stats_snapshots DROP COLUMN IF EXISTS payin_requests_total"
    )
