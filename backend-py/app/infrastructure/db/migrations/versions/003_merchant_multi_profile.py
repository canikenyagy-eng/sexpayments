"""allow multiple merchants per user, add name column, add merchant_stats_snapshots

Revision ID: 003
Revises: 002
Create Date: 2026-04-11

Made idempotent for re-runs on partially-applied schemas.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.infrastructure.db.migrations._helpers import (
    enum_exists,
    has_column,
    has_enum_value,
    has_index,
    has_table,
)

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    # Add 'pending' value to merchantstatus or terminalstatus enum (legacy DBs).
    if enum_exists(bind, "merchantstatus") and not has_enum_value(
        bind, "merchantstatus", "pending"
    ):
        op.execute("ALTER TYPE merchantstatus ADD VALUE 'pending'")
    elif enum_exists(bind, "terminalstatus") and not has_enum_value(
        bind, "terminalstatus", "pending"
    ):
        op.execute("ALTER TYPE terminalstatus ADD VALUE 'pending'")

    if has_table(bind, "merchants") and not has_column(bind, "merchants", "name"):
        op.add_column("merchants", sa.Column("name", sa.String(length=255), nullable=True))

    # Drop the legacy unique constraint on merchants.user_id (allows multiple
    # merchants per user). Use raw SQL with IF EXISTS for safety.
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'merchants' AND constraint_name = 'merchants_user_id_key'
            ) THEN
                ALTER TABLE merchants DROP CONSTRAINT merchants_user_id_key;
            END IF;
        END
        $$;
        """
    )

    if has_table(bind, "merchants") and not has_index(
        bind, "merchants", "ix_merchants_user_id"
    ):
        op.create_index("ix_merchants_user_id", "merchants", ["user_id"])

    if not has_table(bind, "merchant_stats_snapshots"):
        op.create_table(
            "merchant_stats_snapshots",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("merchant_id", sa.Integer(), nullable=False),
            sa.Column("turnover_usdt", sa.Numeric(20, 4), nullable=False, server_default="0"),
            sa.Column("fee_usdt", sa.Numeric(20, 4), nullable=False, server_default="0"),
            sa.Column("orders_total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("orders_success", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("orders_active", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("orders_failed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("conversion_pct", sa.Numeric(8, 4), nullable=False, server_default="0"),
            sa.Column("pending_withdrawals", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("active_disputes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("data_cutoff_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("merchant_id", name="uq_merchant_stats_snapshot"),
        )
        if not has_index(bind, "merchant_stats_snapshots", "ix_merchant_stats_snapshots_id"):
            op.create_index(op.f("ix_merchant_stats_snapshots_id"), "merchant_stats_snapshots", ["id"])
        if not has_index(bind, "merchant_stats_snapshots", "ix_merchant_stats_snapshots_merchant_id"):
            op.create_index(op.f("ix_merchant_stats_snapshots_merchant_id"), "merchant_stats_snapshots", ["merchant_id"])


def downgrade() -> None:
    bind = op.get_bind()

    if has_table(bind, "merchant_stats_snapshots"):
        op.drop_table("merchant_stats_snapshots")

    if has_index(bind, "merchants", "ix_merchants_user_id"):
        op.drop_index("ix_merchants_user_id", table_name="merchants")

    # Re-add the legacy unique constraint only if it doesn't exist.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.table_constraints
                WHERE table_name = 'merchants' AND constraint_name = 'merchants_user_id_key'
            ) THEN
                ALTER TABLE merchants ADD CONSTRAINT merchants_user_id_key UNIQUE (user_id);
            END IF;
        END
        $$;
        """
    )

    if has_column(bind, "merchants", "name"):
        op.drop_column("merchants", "name")
