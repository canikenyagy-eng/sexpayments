"""add merchant_id column to withdrawal_requests for cross-terminal withdrawals

Revision ID: 016
Revises: 015

Made idempotent for re-runs on partially-applied schemas.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.infrastructure.db.migrations._helpers import (
    has_column,
    has_index,
)

revision: str = "016"
down_revision: Union[str, None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    if not has_column(bind, "withdrawal_requests", "merchant_id"):
        op.add_column(
            "withdrawal_requests",
            sa.Column("merchant_id", sa.Integer(), nullable=True),
        )
        op.create_foreign_key(
            None,
            "withdrawal_requests",
            "merchants",
            ["merchant_id"],
            ["id"],
        )

    if not has_index(bind, "withdrawal_requests", "ix_withdrawal_requests_merchant_id"):
        op.create_index(
            "ix_withdrawal_requests_merchant_id",
            "withdrawal_requests",
            ["merchant_id"],
        )

    # Data backfill — safe to re-run because of the IS NULL guard.
    op.execute(
        """
        UPDATE withdrawal_requests wr
        SET
            merchant_id = wr.user_id,
            user_id = m.user_id
        FROM merchants m
        WHERE UPPER(wr.user_role::text) = 'MERCHANT'
          AND wr.merchant_id IS NULL
          AND m.id = wr.user_id;
        """
    )


def downgrade() -> None:
    bind = op.get_bind()

    op.execute(
        """
        UPDATE withdrawal_requests
        SET user_id = merchant_id
        WHERE UPPER(user_role::text) = 'MERCHANT' AND merchant_id IS NOT NULL;
        """
    )
    if has_index(bind, "withdrawal_requests", "ix_withdrawal_requests_merchant_id"):
        op.drop_index(
            "ix_withdrawal_requests_merchant_id",
            table_name="withdrawal_requests",
        )
    if has_column(bind, "withdrawal_requests", "merchant_id"):
        op.drop_column("withdrawal_requests", "merchant_id")
