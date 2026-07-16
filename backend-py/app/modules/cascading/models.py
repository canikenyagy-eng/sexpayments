from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from app.common.enums.cascading import CascadeAttemptStatus, CascadeRateSource
from app.common.types import utcnow
from app.infrastructure.db.base import Base


cascade_group_providers = Table(
    "cascade_group_providers",
    Base.metadata,
    Column(
        "group_id",
        Integer,
        ForeignKey("cascade_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "provider_id",
        Integer,
        ForeignKey("cascade_providers.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


cascade_group_merchants = Table(
    "cascade_group_merchants",
    Base.metadata,
    Column(
        "group_id",
        Integer,
        ForeignKey("cascade_groups.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "merchant_id",
        Integer,
        ForeignKey("merchants.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class CascadeProvider(Base):
    """External P2P platform that issues requisites on demand.

    Each provider is bound 1:1 to a "virtual" User (role=trader, is_system=true)
    and Trader. The virtual user's USDT WORK balance represents our balance with
    the provider — escrow flows reuse the existing FinanceService unchanged.
    """

    __tablename__ = "cascade_providers"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(64), nullable=False, unique=True, index=True)
    name = Column(String(255), nullable=False)
    adapter_type = Column(String(64), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, server_default="true")

    # Endpoint and credentials (api_secret/webhook_secret encrypted via Fernet,
    # same scheme as Merchant.api_secret).
    base_url = Column(String(512), nullable=False)
    api_key_encrypted = Column(String(1024), nullable=True)
    api_secret_encrypted = Column(String(1024), nullable=True)
    webhook_secret_encrypted = Column(String(1024), nullable=True)

    # Virtual trader binding (created together with the provider).
    virtual_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, unique=True)
    virtual_trader_id = Column(Integer, ForeignKey("traders.id"), nullable=False, unique=True)

    # Per-provider rates and per-method fees. Adapters consult these unless the
    # remote response carries its own rate (then the response wins).
    # rates: {"RUB": 95.4, ...}, fees: {"sbp": 1.5, "card": 2.0}
    rates = Column(JSONB, nullable=False, default=dict, server_default="{}")
    fees = Column(JSONB, nullable=False, default=dict, server_default="{}")

    # Hard amount limits — providers below/above these are skipped before request.
    min_amount_fiat = Column(Numeric(15, 2), nullable=True)
    max_amount_fiat = Column(Numeric(15, 2), nullable=True)

    # Circuit breaker — all configurable per-provider (see circuit_breaker.py).
    cb_window_seconds = Column(Integer, nullable=False, default=300, server_default="300")
    cb_threshold_failures = Column(Integer, nullable=False, default=5, server_default="5")
    cb_threshold_rate = Column(Float, nullable=False, default=0.5, server_default="0.5")
    cb_cooldown_seconds = Column(Integer, nullable=False, default=600, server_default="600")
    disabled_until = Column(DateTime(timezone=True), nullable=True)

    # Network timeouts for individual adapter calls (ms).
    request_timeout_ms = Column(Integer, nullable=False, default=3000, server_default="3000")
    cancel_timeout_ms = Column(Integer, nullable=False, default=2000, server_default="2000")

    # Manual ranking weight used by POOLED scoring (higher = preferred).
    priority_weight = Column(Integer, nullable=False, default=100, server_default="100")

    # Where to source the rate for cascade orders going through this provider.
    # See CascadeRateSource for semantics.
    rate_source = Column(
        Enum(
            CascadeRateSource,
            name="cascaderatesource",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=CascadeRateSource.PROVIDER,
        server_default=CascadeRateSource.PROVIDER.value,
    )
    # Used only when rate_source = PLATFORM. Same RateConfig table as merchant rates.
    rate_config_id = Column(
        Integer, ForeignKey("rate_configs.id"), nullable=True
    )

    # Free-form per-provider settings (mapping overrides, feature flags, etc).
    # Shape is defined by the adapter's SETTINGS_SCHEMA — the admin UI builds a
    # form from that schema instead of asking the user to write raw JSON.
    settings = Column(JSONB, nullable=False, default=dict, server_default="{}")

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    groups = relationship(
        "CascadeGroup",
        secondary=cascade_group_providers,
        back_populates="providers",
        lazy="selectin",
    )
    virtual_user = relationship("User", foreign_keys=[virtual_user_id], lazy="selectin")
    virtual_trader = relationship("Trader", foreign_keys=[virtual_trader_id], lazy="selectin")


class CascadeGroup(Base):
    """Cascade tier — a unit of parallelism in GROUPED mode.

    Within a group all providers race in parallel and the first WON wins; losers
    receive cancel_request through their adapter. Groups are ordered by tier
    (1, 2, …) and processed sequentially with a per-group timeout budget.
    """

    __tablename__ = "cascade_groups"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(String(255), nullable=True)
    tier = Column(Integer, nullable=False, default=1, server_default="1", index=True)
    timeout_ms = Column(Integer, nullable=False, default=3000, server_default="3000")
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    providers = relationship(
        "CascadeProvider",
        secondary=cascade_group_providers,
        back_populates="groups",
        lazy="selectin",
    )
    merchants = relationship(
        "Merchant",
        secondary=cascade_group_merchants,
        back_populates="cascade_groups",
        lazy="selectin",
    )


class CascadeOrderAttempt(Base):
    """Per-(order, provider) attempt log — fuels metrics, debug, and dispute trail.

    A single payin order can produce multiple attempts (different tiers / pool
    iteration / hybrid races). Exactly one attempt per order has status=WON.
    """

    __tablename__ = "cascade_order_attempts"

    id = Column(Integer, primary_key=True, index=True)
    # Nullable on the way in — CascadingService persists the attempt before
    # OrderService has the order row, then OrderService stamps order_id
    # right after Order.create returns. By the time the merchant looks at
    # debug info every winning attempt has order_id populated.
    order_id = Column(
        Integer,
        ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    provider_id = Column(
        Integer, ForeignKey("cascade_providers.id"), nullable=False, index=True
    )
    group_id = Column(
        Integer, ForeignKey("cascade_groups.id"), nullable=True, index=True
    )
    tier = Column(Integer, nullable=True)

    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    latency_ms = Column(Integer, nullable=True)

    status = Column(
        Enum(
            CascadeAttemptStatus,
            name="cascadeattemptstatus",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=CascadeAttemptStatus.IN_FLIGHT,
        server_default=CascadeAttemptStatus.IN_FLIGHT.value,
        index=True,
    )
    refusal_reason = Column(String(255), nullable=True)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)

    # Provider response when status in (WON, LOST) — full requisite snapshot.
    external_order_id = Column(String(255), nullable=True, index=True)
    requisite_snapshot = Column(JSONB, nullable=True)
    requisite_id = Column(
        Integer, ForeignKey("requisites.id", ondelete="SET NULL"), nullable=True
    )
    provider_rate = Column(Numeric(15, 4), nullable=True)
    provider_fee_usdt = Column(Numeric(15, 4), nullable=True)
    our_profit_usdt = Column(Numeric(15, 4), nullable=True)  # may be negative

    # Idempotency token sent to the provider — survives retries / cancel races.
    idempotency_key = Column(String(64), nullable=False, index=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    provider = relationship("CascadeProvider", foreign_keys=[provider_id], lazy="joined")
    group = relationship("CascadeGroup", foreign_keys=[group_id], lazy="select")

    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "external_order_id",
            name="uq_cascade_attempt_provider_external",
        ),
    )


class CascadeProviderMetric(Base):
    """Hourly aggregate of CascadeOrderAttempt for admin metrics charts."""

    __tablename__ = "cascade_provider_metrics"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(
        Integer, ForeignKey("cascade_providers.id", ondelete="CASCADE"), nullable=False
    )
    bucket_at = Column(DateTime(timezone=True), nullable=False, index=True)

    request_count = Column(Integer, nullable=False, default=0, server_default="0")
    success_count = Column(Integer, nullable=False, default=0, server_default="0")
    failure_count = Column(Integer, nullable=False, default=0, server_default="0")
    timeout_count = Column(Integer, nullable=False, default=0, server_default="0")
    cancel_count = Column(Integer, nullable=False, default=0, server_default="0")
    total_latency_ms = Column(Integer, nullable=False, default=0, server_default="0")
    total_volume_usdt = Column(
        Numeric(20, 4), nullable=False, default=0, server_default="0"
    )
    total_profit_usdt = Column(
        Numeric(20, 4), nullable=False, default=0, server_default="0"
    )

    __table_args__ = (
        UniqueConstraint("provider_id", "bucket_at", name="uq_cascade_metric_bucket"),
    )


class ProviderCallbackAttempt(Base):
    """Persisted record of an inbound provider → us callback.

    One row per HTTP POST to ``/api/cascade/v1/callbacks/{provider_code}``.
    Stored even when the call failed (bad signature / unknown order / parse
    error) so the admin Callbacks page surfaces ALL provider traffic, not
    only the happy path.

    ``provider_id`` / ``order_id`` are nullable on purpose: a request can
    arrive for an unknown provider code (signature check rejects before we
    resolve the row) or for an external_order_id we don't recognise. The
    log row still has to be saved so we can investigate.
    """

    __tablename__ = "provider_callback_attempts"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(
        Integer,
        ForeignKey("cascade_providers.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider_code = Column(String(64), nullable=False, index=True)
    order_id = Column(
        Integer,
        ForeignKey("orders.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    external_order_id = Column(String(255), nullable=True, index=True)

    # Raw incoming request — kept verbatim for debugging signature/format
    # mismatches with the provider's side.
    request_headers = Column(JSONB, nullable=True)
    request_body = Column(Text, nullable=True)

    # Signature passed adapter verification. False rows are kept so the
    # admin sees when someone replays or tampers with a callback.
    signature_valid = Column(Boolean, nullable=False, default=False)

    # ParsedCallback.status.value when we successfully parsed the body.
    parsed_status = Column(String(64), nullable=True)

    # The HTTP response we sent back.
    response_status = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)

    # Populated when parse / apply raised — keep the message for the admin
    # UI to show alongside the row.
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False, index=True)

    provider = relationship(
        "CascadeProvider", foreign_keys=[provider_id], lazy="joined"
    )
