"""Drop excluded column from order_creation_snapshots

Revision ID: 023
Revises: 022
Create Date: 2026-04-29 12:00:00.000000

Made idempotent for re-runs on partially-applied schemas.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from app.infrastructure.db.migrations._helpers import has_column


# revision identifiers, used by Alembic.
revision: str = '023'
down_revision: Union[str, None] = '022'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "order_creation_snapshots", "excluded"):
        op.drop_column('order_creation_snapshots', 'excluded')


def downgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "order_creation_snapshots", "excluded"):
        op.add_column(
            'order_creation_snapshots',
            sa.Column('excluded', sa.JSON(), autoincrement=False, nullable=False, server_default='[]'),
        )
