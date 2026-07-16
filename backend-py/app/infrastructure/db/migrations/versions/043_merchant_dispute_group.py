"""merchants.dispute_telegram_group_id (merchant-dispute-bot intake chat)

Revision ID: 043
Revises: 042
Create Date: 2026-06-06

Binds a Telegram group/channel to a merchant for the new ``merchant-dispute-bot``.
The merchant (or their staff) drops a message with an order identifier
(uuid / external_id) + a receipt file into that chat; the bot resolves the
merchant by this chat_id and feeds the file into the normal receipt
premoderation flow (OrderService.confirm_order).

NULL — the merchant has no dispute-intake chat bound. Mirrors the existing
``notify_telegram_group_id`` (which is the OUTBOUND merchant-notify-bot chat).
Idempotent: ADD/DROP COLUMN IF [NOT] EXISTS.
"""
from alembic import op

revision = "043"
down_revision = "042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE merchants "
        "ADD COLUMN IF NOT EXISTS dispute_telegram_group_id BIGINT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE merchants DROP COLUMN IF EXISTS dispute_telegram_group_id"
    )
