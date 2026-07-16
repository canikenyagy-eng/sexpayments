"""add stats_snapshots and teamlead_links tables

Revision ID: 002
Revises: 001
Create Date: 2026-04-10

Made idempotent for re-runs on partially-applied schemas.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.infrastructure.db.migrations._helpers import (
    has_index,
    has_table,
)

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

userrole_enum = sa.Enum(
    "admin", "merchant", "trader", "teamlead",
    name="userrole",
)


def upgrade() -> None:
    bind = op.get_bind()

    if not has_table(bind, "stats_snapshots"):
        op.create_table(
            "stats_snapshots",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("turnover_usdt", sa.Numeric(20, 4), nullable=False, server_default="0"),
            sa.Column("profit_usdt", sa.Numeric(20, 4), nullable=False, server_default="0"),
            sa.Column("requests_usdt", sa.Numeric(20, 4), nullable=False, server_default="0"),
            sa.Column("orders_total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("orders_success", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("orders_active", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("payout_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("payout_pct", sa.Numeric(8, 4), nullable=False, server_default="0"),
            sa.Column("conversion_pct", sa.Numeric(8, 4), nullable=False, server_default="0"),
            sa.Column("merchants_online_24h", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("traders_online_24h", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("pending_withdrawals", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("active_disputes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("data_cutoff_at", sa.DateTime(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        if not has_index(bind, "stats_snapshots", "ix_stats_snapshots_id"):
            op.create_index(op.f("ix_stats_snapshots_id"), "stats_snapshots", ["id"])

    if not has_table(bind, "teamlead_links"):
        op.create_table(
            "teamlead_links",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("teamlead_id", sa.Integer(), nullable=False),
            sa.Column("linked_entity_type", userrole_enum, nullable=False),
            sa.Column("linked_entity_id", sa.Integer(), nullable=False),
            sa.Column("fee_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(
                ["teamlead_id"], ["users.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        if not has_index(bind, "teamlead_links", "ix_teamlead_links_id"):
            op.create_index(op.f("ix_teamlead_links_id"), "teamlead_links", ["id"])
        if not has_index(bind, "teamlead_links", "ix_teamlead_links_teamlead_id"):
            op.create_index(op.f("ix_teamlead_links_teamlead_id"), "teamlead_links", ["teamlead_id"])
        if not has_index(bind, "teamlead_links", "ix_teamlead_links_linked_entity_type"):
            op.create_index(op.f("ix_teamlead_links_linked_entity_type"), "teamlead_links", ["linked_entity_type"])
        if not has_index(bind, "teamlead_links", "ix_teamlead_links_linked_entity_id"):
            op.create_index(op.f("ix_teamlead_links_linked_entity_id"), "teamlead_links", ["linked_entity_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if has_table(bind, "teamlead_links"):
        op.drop_table("teamlead_links")
    if has_table(bind, "stats_snapshots"):
        op.drop_table("stats_snapshots")
