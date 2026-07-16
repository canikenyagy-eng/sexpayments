"""drop dispute_comments table (comments/correspondence removed)

Revision ID: 037
Revises: 036
Create Date: 2026-06-04

The dispute comment thread (admin/trader/merchant correspondence) is removed
from the product. Disputes keep their core fields (reason, description,
evidence_files, resolution_text); only the back-and-forth ``dispute_comments``
table is dropped.

Idempotent: guarded with IF EXISTS so re-running is a no-op.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "037"
down_revision = "036"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The FK from dispute_comments → disputes is ON DELETE CASCADE and lives on
    # this table, so dropping the table removes it (and its indexes) cleanly.
    op.execute("DROP TABLE IF EXISTS dispute_comments CASCADE")


def downgrade() -> None:
    # Recreate the table structure (data is not restored). ``userrole`` is an
    # existing enum type — reference it without recreating.
    op.create_table(
        "dispute_comments",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "dispute_id",
            sa.Integer(),
            sa.ForeignKey("disputes.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "author_type",
            postgresql.ENUM(name="userrole", create_type=False),
            nullable=False,
        ),
        sa.Column("author_id", sa.Integer(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("files", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
