"""add unique constraint on teamlead_links (teamlead_id, linked_entity_type, linked_entity_id)

Revision ID: 017
Revises: 016

Made idempotent for re-runs on partially-applied schemas.
"""
from typing import Sequence, Union

from alembic import op

from app.infrastructure.db.migrations._helpers import (
    has_table,
    has_unique_constraint,
)

revision: str = "017"
down_revision: Union[str, None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    if not has_table(bind, "teamlead_links"):
        return

    # Drop duplicates that would conflict with the new unique constraint.
    op.execute(
        """
        WITH duplicates AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY teamlead_id, linked_entity_type, linked_entity_id
                       ORDER BY is_active DESC, created_at DESC, id DESC
                   ) AS rn
            FROM teamlead_links
        )
        DELETE FROM teamlead_links
        WHERE id IN (SELECT id FROM duplicates WHERE rn > 1);
        """
    )

    if not has_unique_constraint(
        bind, "teamlead_links", "uq_teamlead_link_teamlead_entity"
    ):
        op.create_unique_constraint(
            "uq_teamlead_link_teamlead_entity",
            "teamlead_links",
            ["teamlead_id", "linked_entity_type", "linked_entity_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_unique_constraint(
        bind, "teamlead_links", "uq_teamlead_link_teamlead_entity"
    ):
        op.drop_constraint(
            "uq_teamlead_link_teamlead_entity",
            "teamlead_links",
            type_="unique",
        )
