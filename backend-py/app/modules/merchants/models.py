from sqlalchemy import BigInteger, Boolean, Column, Integer, String, ForeignKey, Enum, Numeric, JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from app.infrastructure.db.base import Base
from app.common.enums.cascading import CascadeMode
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
# Importing the M:N tables here ensures they're registered against
# Base.metadata before SQLAlchemy resolves Merchant.cascade_groups via its
# string secondary name. Without this, tests that import only the merchants
# module fail with "expression 'cascade_group_merchants' failed to locate a name".
from app.modules.cascading.models import (  # noqa: F401
    cascade_group_merchants,
    cascade_group_providers,
)
from app.modules.traders.models import merchant_trader_groups, trader_merchants


class Merchant(Base):
    __tablename__ = "merchants"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=True)

    # Telegram user IDs allowed to use this merchant via the bot
    telegram_user_ids = Column(JSONB, nullable=False, default=list, server_default="[]")

    status = Column(
        Enum(TerminalStatus, name="merchantstatus", values_callable=lambda x: [e.value for e in x]),
        default=TerminalStatus.PENDING,
        nullable=False,
    )
    currency = Column(Enum(Currency), default=Currency.RUB, nullable=False)
    
    # Webhook settings
    webhook_url = Column(String(255), nullable=True)
    api_key = Column(String(255), nullable=False, unique=True, index=True)
    api_secret = Column(String(255), nullable=False)
    
    # Order processing settings
    requisite_search_timeout_ms = Column(Integer, default=300, nullable=False)
    order_ttl_seconds = Column(Integer, default=1800, nullable=False)
    
    # Which rate config this merchant uses for currency conversion
    rate_config_id = Column(Integer, ForeignKey("rate_configs.id"), nullable=True)
    
    # Rates/Fees mapping: {"card": 2.5, "sbp": 2.0}
    fees = Column(JSON, default=dict, nullable=False)
    
    # Fixed withdrawal fee in USDT
    withdrawal_fee_fixed = Column(Numeric(15, 2), default=0, nullable=False)

    # Payout config moved to the dedicated PayoutTerminal (commission_percent /
    # ttl_minutes / rate_config) — payouts no longer run on the payin merchant.

    # Cascade (external provider routing): off / grouped / pooled.
    # GROUPED uses cascade_groups attached via cascade_group_merchants.
    # POOLED ignores groups and walks all active providers ranked by score.
    cascade_mode = Column(
        Enum(CascadeMode, name="merchantcascademode", values_callable=lambda x: [e.value for e in x]),
        default=CascadeMode.OFF,
        nullable=False,
        server_default=CascadeMode.OFF.value,
    )

    # Receipt premoderation (support-bot pre-trader review).
    # NULL — use the platform-wide ``receipt_premoderation_enabled`` flag
    # (default behaviour for most merchants). Set explicitly to TRUE/FALSE
    # to override the global setting for this merchant.
    receipt_premoderation_enabled = Column(Boolean, nullable=True)

    # Telegram chat_id of the group where ``merchant-notify-bot`` posts
    # premoderation-rejection notifications ("Запросить PDF/Видео").
    # When NULL the merchant simply doesn't get a bot notification — the
    # rejection is still visible via ``orders.moderation_status``.
    notify_telegram_group_id = Column(BigInteger, nullable=True)

    # Telegram chat_id of the group/channel where ``merchant-dispute-bot`` reads
    # inbound messages (order uuid/external_id + receipt file) posted by the
    # merchant or their staff. The bot resolves the merchant by this chat_id and
    # feeds the file into the normal receipt premoderation flow. NULL — no
    # dispute-intake chat bound (the bot ignores messages from that chat).
    dispute_telegram_group_id = Column(BigInteger, nullable=True)

    # Per-merchant token-position mask to pull OUR order id out of a free-form
    # appeal message (the merchant's own id often appears first). Grammar:
    #   "N" / "word:N" → the N-th whitespace token (1-based)
    #   "uuid:N"       → the N-th UUID-shaped token
    # The picked token is resolved against our DB (uuid → external_id); if it
    # doesn't resolve we scan every token, so a slightly-off mask still works.
    # NULL — no mask (scan all tokens).
    dispute_id_mask = Column(String(50), nullable=True)

    # When ON, the admin order modal shows a «Клиент» block (our internal client
    # public_id + all-time deals/conversion) for THIS merchant's orders. OFF
    # (default) — the block is neither rendered nor sent over the API.
    unique_clients_enabled = Column(Boolean, nullable=False, default=False, server_default="false")

    # When ON (default), a PDF/video proof request is also pushed to the merchant's
    # chat: the merchant-notify-bot text message, or — for BOT-created orders — a
    # merchant-bot message with an "attach proof" button. OFF — only the API
    # webhook carries the request (dispute.substatus pdf_requested/video_requested).
    proof_request_notify_enabled = Column(Boolean, nullable=False, default=True, server_default="true")

    trader_groups = relationship("TraderGroup", secondary=merchant_trader_groups, back_populates="merchants", lazy="selectin")
    traders = relationship("Trader", secondary=trader_merchants, back_populates="merchants", lazy="selectin")
    cascade_groups = relationship(
        "CascadeGroup",
        secondary="cascade_group_merchants",
        back_populates="merchants",
        lazy="selectin",
    )



