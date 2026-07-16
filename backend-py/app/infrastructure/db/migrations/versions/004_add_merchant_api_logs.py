"""add merchant_api_logs table if missing

Revision ID: 004
Revises: 003
Create Date: 2026-04-11

Made idempotent for re-runs on partially-applied schemas.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.infrastructure.db.migrations._helpers import (
    has_index,
    has_table,
)

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    if not has_table(bind, "merchant_api_logs"):
        op.create_table(
            "merchant_api_logs",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("merchant_id", sa.Integer(), nullable=True),
            sa.Column("order_id", sa.Integer(), nullable=True),
            sa.Column("url", sa.String(255), nullable=False),
            sa.Column("method", sa.String(10), nullable=False),
            sa.Column("request_headers", sa.JSON(), nullable=True),
            sa.Column("request_body", sa.Text(), nullable=True),
            sa.Column("response_status", sa.Integer(), nullable=True),
            sa.Column("response_headers", sa.JSON(), nullable=True),
            sa.Column("response_body", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["merchant_id"], ["merchants.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        if not has_index(bind, "merchant_api_logs", "ix_merchant_api_logs_id"):
            op.create_index(op.f("ix_merchant_api_logs_id"), "merchant_api_logs", ["id"])
        if not has_index(bind, "merchant_api_logs", "ix_merchant_api_logs_merchant_id"):
            op.create_index(op.f("ix_merchant_api_logs_merchant_id"), "merchant_api_logs", ["merchant_id"])
        if not has_index(bind, "merchant_api_logs", "ix_merchant_api_logs_order_id"):
            op.create_index(op.f("ix_merchant_api_logs_order_id"), "merchant_api_logs", ["order_id"])


def downgrade() -> None:
    bind = op.get_bind()
    if has_table(bind, "merchant_api_logs"):
        op.drop_table("merchant_api_logs")
