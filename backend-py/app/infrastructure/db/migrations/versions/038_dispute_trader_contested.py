"""add disputes.trader_contested_at (trader contests → escalate to admin)

Revision ID: 038
Revises: 037
Create Date: 2026-06-04

Variant A trader self-service on disputes: the assigned trader can either
"accept" (concede → dispute resolved in the merchant's favour) or "contest"
(the dispute stays OPEN and is escalated to admin for the final decision).
This column records when the trader contested, so both the trader and admin
UIs can show that the trader pushed back.

Idempotent: guarded with IF NOT EXISTS.
"""
from alembic import op

revision = "038"
down_revision = "037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE disputes "
        "ADD COLUMN IF NOT EXISTS trader_contested_at TIMESTAMP WITH TIME ZONE"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE disputes DROP COLUMN IF EXISTS trader_contested_at")
