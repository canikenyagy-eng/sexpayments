"""orders.provider_order_id (provider's order id for cascade-routed deals)

Revision ID: 045
Revises: 044
Create Date: 2026-06-07

Stores the cascade provider's order id (the won attempt's external_order_id) on
the order. Its presence is the marker that a deal was routed through a cascade
provider. NULL — local (trader) deal.

Idempotent: ADD/DROP COLUMN IF [NOT] EXISTS.
"""
from alembic import op

revision = "045"
down_revision = "044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE orders "
        "ADD COLUMN IF NOT EXISTS provider_order_id VARCHAR(255)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE orders DROP COLUMN IF EXISTS provider_order_id"
    )
