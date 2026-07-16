"""achievements: add ``streak_volume_tier`` to the rule-type enum

Revision ID: 070
Revises: 069
Create Date: 2026-07-07

The achievements model was unified: one bonus = a streak GATE + a turnover LEVEL
by AVERAGE daily volume over the streak window. The new rule type
``streak_volume_tier`` replaces the legacy ``consecutive_days_volume`` /
``daily_volume_tier`` (kept as dead enum values — PG enum values can't be dropped
cleanly and nothing produces them anymore).

``trader_achievements.rule_type`` is the PG enum ``achievementruletype``; add the
new value so the breakdown rows can be written. PG-only; SQLite (tests) builds the
column's CHECK from the model enum, which already lists the value.
"""
from alembic import op

revision = "070"
down_revision = "069"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    op.execute("ALTER TYPE achievementruletype ADD VALUE IF NOT EXISTS 'streak_volume_tier'")


def downgrade() -> None:
    # PostgreSQL can't drop a value from an enum type; leaving it is harmless.
    pass
