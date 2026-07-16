"""requisite & trader priority: weighted-pooling columns

Revision ID: 071
Revises: 070
Create Date: 2026-07-11

Backing store for the WEIGHTED pooling strategy (deals distributed by priority):
  * ``requisites.trader_priority`` — trader-set weight (1-3), default 1.
  * ``requisites.priority_score`` — denormalized computed score (Numeric(12,4),
    default 100), recomputed off the order hot path per (trader, currency, method)
    group by ``PriorityService``; order creation only reads it.
  * ``traders.priority_bonus_percent`` — admin-set per-trader % lever (default 0).

Idempotent (guards via ``_helpers.has_column``). Existing rows keep the defaults,
which already equal the "all weights equal, 0% bonus" score of 100 — no backfill.
"""
import sqlalchemy as sa
from alembic import op

from app.infrastructure.db.migrations._helpers import has_column

revision = "071"
down_revision = "070"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "requisites", "trader_priority"):
        op.add_column("requisites", sa.Column("trader_priority", sa.Integer(), nullable=False, server_default="1"))
    if not has_column(bind, "requisites", "priority_score"):
        op.add_column("requisites", sa.Column("priority_score", sa.Numeric(12, 4), nullable=False, server_default="100"))
    if not has_column(bind, "traders", "priority_bonus_percent"):
        op.add_column("traders", sa.Column("priority_bonus_percent", sa.Numeric(10, 2), nullable=False, server_default="0"))


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "traders", "priority_bonus_percent"):
        op.drop_column("traders", "priority_bonus_percent")
    if has_column(bind, "requisites", "priority_score"):
        op.drop_column("requisites", "priority_score")
    if has_column(bind, "requisites", "trader_priority"):
        op.drop_column("requisites", "trader_priority")
