"""receipt premoderation: platform_settings, merchants override, orders state, receipt_moderations log

Revision ID: 034
Revises: 033
Create Date: 2026-05-25

Adds the support-bot premoderation layer:
  * platform_settings — generic key/value table for global toggles
    (re-introduced here; previously ghost-coded but never migrated).
    Seeded with ``receipt_premoderation_enabled`` and ``support_bot_chat_id``.
  * merchants.receipt_premoderation_enabled — optional per-merchant
    override of the global flag (NULL → use global).
  * merchants.notify_telegram_group_id — group id where
    ``merchant-notify-bot`` posts PDF/Video re-upload requests.
  * orders.moderation_status — per-order pre-trader review state (see
    ``app.common.enums.receipt_moderations.ModerationStatus``).
  * receipt_moderations — one row per moderation cycle; first-wins
    finalisation via ``orders.moderation_status='pending'`` precondition.

Idempotent: same convention as 029/030 — every create/alter step checks
the existing schema first so the migration survives partial reruns.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "034"
down_revision = "033"
branch_labels = None
depends_on = None


_MODERATION_STATUS_ENUM = postgresql.ENUM(
    "none",
    "pending",
    "approved",
    "pdf_requested",
    "video_requested",
    name="moderationstatus",
    create_type=False,
)

_MODERATION_DECISION_ENUM = postgresql.ENUM(
    "accept",
    "request_pdf",
    "request_video",
    name="moderationdecision",
    create_type=False,
)


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _MODERATION_STATUS_ENUM.create(bind=bind, checkfirst=True)
    _MODERATION_DECISION_ENUM.create(bind=bind, checkfirst=True)

    # --- platform_settings -------------------------------------------------

    if not _has_table(inspector, "platform_settings"):
        op.create_table(
            "platform_settings",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("key", sa.String(length=128), nullable=False, unique=True),
            sa.Column("value", sa.Text(), nullable=True),
            sa.Column("description", sa.String(length=512), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
        )
        op.create_index(
            "ix_platform_settings_key",
            "platform_settings",
            ["key"],
            unique=True,
        )

    # Seed the two known keys with their defaults so the admin UI can
    # always render an editor row even before anyone has touched them.
    # ON CONFLICT DO NOTHING keeps re-runs idempotent.
    op.execute(
        sa.text(
            """
            INSERT INTO platform_settings (key, value, description, created_at, updated_at)
            VALUES
              ('receipt_premoderation_enabled', 'false',
               'Глобальный тоггл премодерации чеков саппорт-ботом.',
               NOW(), NOW()),
              ('support_bot_chat_id', '',
               'Telegram chat_id группы для support-bot модерации.',
               NOW(), NOW())
            ON CONFLICT (key) DO NOTHING
            """
        )
    )

    # --- merchants overrides ----------------------------------------------

    if not _has_column(inspector, "merchants", "receipt_premoderation_enabled"):
        op.add_column(
            "merchants",
            sa.Column(
                "receipt_premoderation_enabled",
                sa.Boolean(),
                nullable=True,
            ),
        )

    if not _has_column(inspector, "merchants", "notify_telegram_group_id"):
        op.add_column(
            "merchants",
            sa.Column(
                "notify_telegram_group_id",
                sa.BigInteger(),
                nullable=True,
            ),
        )

    # --- orders.moderation_status -----------------------------------------

    if not _has_column(inspector, "orders", "moderation_status"):
        op.add_column(
            "orders",
            sa.Column(
                "moderation_status",
                _MODERATION_STATUS_ENUM,
                nullable=False,
                server_default="none",
            ),
        )

    # --- receipt_moderations ----------------------------------------------

    if not _has_table(inspector, "receipt_moderations"):
        op.create_table(
            "receipt_moderations",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "order_id",
                sa.Integer(),
                sa.ForeignKey("orders.id", ondelete="CASCADE"),
                nullable=False,
            ),
            # Telegram chat the bot delivered the receipt to. Kept here
            # (rather than re-derived from PlatformSetting at click time)
            # so that historical moderation rows stay accurate even if the
            # admin later switches the bot to a different chat.
            sa.Column("chat_id", sa.BigInteger(), nullable=False),
            # Back-filled by the worker after the bot replies with the
            # message_id of the delivered receipt. The bot uses it to
            # edit the inline keyboard once a decision is taken.
            sa.Column("message_id", sa.BigInteger(), nullable=True),
            sa.Column(
                "decision",
                _MODERATION_DECISION_ENUM,
                nullable=True,
            ),
            sa.Column("moderator_tg_id", sa.BigInteger(), nullable=True),
            sa.Column("moderator_username", sa.String(length=64), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_receipt_moderations_order_id",
            "receipt_moderations",
            ["order_id"],
        )
        # Quick "list pending" query for admin UI.
        op.create_index(
            "ix_receipt_moderations_pending",
            "receipt_moderations",
            ["created_at"],
            postgresql_where=sa.text("decision IS NULL"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "receipt_moderations"):
        op.drop_index("ix_receipt_moderations_pending", table_name="receipt_moderations")
        op.drop_index("ix_receipt_moderations_order_id", table_name="receipt_moderations")
        op.drop_table("receipt_moderations")

    if _has_column(inspector, "orders", "moderation_status"):
        op.drop_column("orders", "moderation_status")

    if _has_column(inspector, "merchants", "notify_telegram_group_id"):
        op.drop_column("merchants", "notify_telegram_group_id")

    if _has_column(inspector, "merchants", "receipt_premoderation_enabled"):
        op.drop_column("merchants", "receipt_premoderation_enabled")

    if _has_table(inspector, "platform_settings"):
        op.drop_index("ix_platform_settings_key", table_name="platform_settings")
        op.drop_table("platform_settings")

    _MODERATION_DECISION_ENUM.drop(bind=bind, checkfirst=True)
    _MODERATION_STATUS_ENUM.drop(bind=bind, checkfirst=True)
