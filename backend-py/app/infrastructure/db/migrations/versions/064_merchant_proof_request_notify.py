"""merchants.proof_request_notify_enabled — gate the chat proof-request push

Adds ``merchants.proof_request_notify_enabled`` — per-merchant toggle (default
ON). When ON, a PDF/video proof request is pushed to the merchant's chat
(merchant-notify-bot text, or a merchant-bot message with an attach button for
bot-created orders). OFF — only the API webhook carries the request.

Revision ID: 064
Revises: 063
Create Date: 2026-07-05
"""
from alembic import op
import sqlalchemy as sa

from app.infrastructure.db.migrations._helpers import has_column

revision = "064"
down_revision = "063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "merchants", "proof_request_notify_enabled"):
        op.add_column(
            "merchants",
            sa.Column(
                "proof_request_notify_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("true"),
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "merchants", "proof_request_notify_enabled"):
        op.drop_column("merchants", "proof_request_notify_enabled")
