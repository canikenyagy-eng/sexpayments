"""trader achievements: daily-volume rollup + per-rule state + trader bonus column

Revision ID: 068
Revises: 067
Create Date: 2026-07-06

Backing store for the trader achievements/bonuses feature:
  * ``trader_daily_volume`` — per-(trader, day) processed-volume rollup, filled by
    background jobs (NOT the hot path); the metric streaks/tiers are computed from.
  * ``trader_achievements`` — current per-rule unlocked level (cabinet breakdown).
  * ``traders.achievement_bonus_percent`` — materialized total bonus read on the
    order hot path (``OrderService._calculate_trader_fee``).
  * index ``ix_orders_status_created`` — so the nightly rollup GROUP BY reads only
    one day's SUCCESS orders instead of a seq scan.

Idempotent (guards via _helpers). Enum create is a PG-only no-op-safe step.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.infrastructure.db.migrations._helpers import has_column, has_index, has_table

revision = "068"
down_revision = "067"
branch_labels = None
depends_on = None

_RULE_ENUM = postgresql.ENUM(
    "consecutive_days_volume",
    "daily_volume_tier",
    name="achievementruletype",
    create_type=False,
)

_ORDERS_IDX = "ix_orders_status_created"


def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
    if is_pg:
        _RULE_ENUM.create(bind=bind, checkfirst=True)
    rule_type_col = _RULE_ENUM if is_pg else sa.String(length=32)

    if not has_table(bind, "trader_daily_volume"):
        op.create_table(
            "trader_daily_volume",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "trader_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("date", sa.Date(), nullable=False),
            sa.Column("amount_usdt", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("amount_rub", sa.Numeric(18, 4), nullable=False, server_default="0"),
            sa.Column("order_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("trader_user_id", "date", name="uq_trader_daily_volume_day"),
        )
    if not has_index(bind, "trader_daily_volume", "ix_trader_daily_volume_trader_user_id"):
        op.create_index("ix_trader_daily_volume_trader_user_id", "trader_daily_volume", ["trader_user_id"])
    if not has_index(bind, "trader_daily_volume", "ix_trader_daily_volume_date"):
        op.create_index("ix_trader_daily_volume_date", "trader_daily_volume", ["date"])

    if not has_table(bind, "trader_achievements"):
        op.create_table(
            "trader_achievements",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "trader_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("rule_type", rule_type_col, nullable=False),
            sa.Column("level_key", sa.String(length=64), nullable=True),
            sa.Column("bonus_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("trader_user_id", "rule_type", name="uq_trader_achievement_rule"),
        )
    if not has_index(bind, "trader_achievements", "ix_trader_achievements_trader_user_id"):
        op.create_index("ix_trader_achievements_trader_user_id", "trader_achievements", ["trader_user_id"])

    if not has_column(bind, "traders", "achievement_bonus_percent"):
        op.add_column(
            "traders",
            sa.Column("achievement_bonus_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
        )

    # Speeds up the once-a-day rollup GROUP BY (WHERE status='success' AND day).
    if not has_index(bind, "orders", _ORDERS_IDX):
        op.create_index(_ORDERS_IDX, "orders", ["status", "created_at"])


def downgrade() -> None:
    bind = op.get_bind()
    if has_index(bind, "orders", _ORDERS_IDX):
        op.drop_index(_ORDERS_IDX, table_name="orders")
    if has_column(bind, "traders", "achievement_bonus_percent"):
        op.drop_column("traders", "achievement_bonus_percent")
    if has_table(bind, "trader_achievements"):
        op.drop_table("trader_achievements")
    if has_table(bind, "trader_daily_volume"):
        op.drop_table("trader_daily_volume")
    if bind.dialect.name == "postgresql":
        _RULE_ENUM.drop(bind=bind, checkfirst=True)
