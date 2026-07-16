"""provider_callback_attempts: persist inbound provider callbacks

Revision ID: 035
Revises: 034
Create Date: 2026-05-26

Adds the ``provider_callback_attempts`` table — one row per inbound POST to
``/api/cascade/v1/callbacks/{provider_code}``. Stored regardless of outcome
(bad signature / unknown order / parse error) so the admin Callbacks page
surfaces ALL provider traffic, not only the happy path.

Idempotent: every create step checks the existing schema first so the
migration survives partial reruns.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "035"
down_revision = "034"
branch_labels = None
depends_on = None


def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_table(inspector, "provider_callback_attempts"):
        op.create_table(
            "provider_callback_attempts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "provider_id",
                sa.Integer(),
                sa.ForeignKey("cascade_providers.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("provider_code", sa.String(length=64), nullable=False),
            sa.Column(
                "order_id",
                sa.Integer(),
                sa.ForeignKey("orders.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("external_order_id", sa.String(length=255), nullable=True),
            sa.Column("request_headers", postgresql.JSONB(), nullable=True),
            sa.Column("request_body", sa.Text(), nullable=True),
            sa.Column(
                "signature_valid",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("false"),
            ),
            sa.Column("parsed_status", sa.String(length=64), nullable=True),
            sa.Column("response_status", sa.Integer(), nullable=True),
            sa.Column("response_body", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_provider_callback_attempts_provider_id",
            "provider_callback_attempts",
            ["provider_id"],
        )
        op.create_index(
            "ix_provider_callback_attempts_provider_code",
            "provider_callback_attempts",
            ["provider_code"],
        )
        op.create_index(
            "ix_provider_callback_attempts_order_id",
            "provider_callback_attempts",
            ["order_id"],
        )
        op.create_index(
            "ix_provider_callback_attempts_external_order_id",
            "provider_callback_attempts",
            ["external_order_id"],
        )
        op.create_index(
            "ix_provider_callback_attempts_created_at",
            "provider_callback_attempts",
            ["created_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "provider_callback_attempts"):
        for ix in (
            "ix_provider_callback_attempts_created_at",
            "ix_provider_callback_attempts_external_order_id",
            "ix_provider_callback_attempts_order_id",
            "ix_provider_callback_attempts_provider_code",
            "ix_provider_callback_attempts_provider_id",
        ):
            op.drop_index(ix, table_name="provider_callback_attempts")
        op.drop_table("provider_callback_attempts")
