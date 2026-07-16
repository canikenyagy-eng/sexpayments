"""admin → all-traders broadcasts

Revision ID: 062
Revises: 061
Create Date: 2026-07-04

Adds the ``broadcasts`` table backing the admin «Рассылка» page: one row per
broadcast job (text + audience + delivered/failed counters + status). Idempotent
(same convention as 030/060): create ops check the existing schema first.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "062"
down_revision = "061"
branch_labels = None
depends_on = None


_AUDIENCE_ENUM = postgresql.ENUM(
    "all", "except_blocked", name="broadcastaudience", create_type=False,
)
_STATUS_ENUM = postgresql.ENUM(
    "pending", "sending", "done", "failed", name="broadcaststatus", create_type=False,
)


def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _AUDIENCE_ENUM.create(bind=bind, checkfirst=True)
    _STATUS_ENUM.create(bind=bind, checkfirst=True)

    if not _has_table(inspector, "broadcasts"):
        op.create_table(
            "broadcasts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "admin_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("audience", _AUDIENCE_ENUM, nullable=False),
            sa.Column("status", _STATUS_ENUM, nullable=False, server_default="pending"),
            sa.Column("total_recipients", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("delivered", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("failed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("ix_broadcasts_admin_user_id", "broadcasts", ["admin_user_id"])
        op.create_index("ix_broadcasts_status", "broadcasts", ["status"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "broadcasts"):
        op.drop_table("broadcasts")
    op.execute("DROP TYPE IF EXISTS broadcaststatus")
    op.execute("DROP TYPE IF EXISTS broadcastaudience")
