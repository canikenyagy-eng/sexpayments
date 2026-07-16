"""cascade module: providers, groups, attempts, metrics + merchant.cascade_mode + requisite.source + user.is_system

Revision ID: 029
Revises: 028
Create Date: 2026-05-08

Adds the cascade routing layer:
  * cascade_providers — external requisite providers (1:1 with virtual user+trader)
  * cascade_groups + cascade_group_providers + cascade_group_merchants — tier
    grouping with per-merchant binding (merchant ↔ group is M:N just like
    merchant_trader_groups)
  * cascade_order_attempts — log of every issue_requisite call (race winners,
    losers, refusals, timeouts) — drives metrics + admin debug
  * cascade_provider_metrics — hourly bucket aggregates (success_rate, latency,
    volume, profit) for the admin dashboards

Also adds three small fields to existing tables:
  * users.is_system           — hides cascade-virtual users from regular trader listings
  * merchants.cascade_mode    — off/grouped/pooled toggle per merchant
  * requisites.source         — local/cascade marker so the pooler skips
                                cascade-issued requisites in future selections

All operations are idempotent — safe to re-run on a database that already has
some of the additions (existing prod paths apply 028 first).
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "029"
down_revision = "028"
branch_labels = None
depends_on = None


# Using postgresql.ENUM (not sa.Enum) so create_type=False is respected — the
# generic sa.Enum will still try to CreateEnumType on op.create_table even
# when we've already provisioned the type via a separate op.execute. We
# create the types explicitly with checkfirst=True so the migration is
# idempotent across partial reruns.
_CASCADE_MODE_ENUM = postgresql.ENUM(
    "off",
    "grouped",
    "pooled",
    name="merchantcascademode",
    create_type=False,
)
_REQUISITE_SOURCE_ENUM = postgresql.ENUM(
    "local",
    "cascade",
    name="requisitesource",
    create_type=False,
)
_CASCADE_ATTEMPT_STATUS_ENUM = postgresql.ENUM(
    "in_flight",
    "won",
    "lost",
    "cancelled",
    "refused",
    "timeout",
    "error",
    name="cascadeattemptstatus",
    create_type=False,
)


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Provision all enum types up-front, idempotently. checkfirst=True asks
    # PostgreSQL to skip CREATE if the type already exists (covers partial
    # reruns where a previous attempt aborted mid-table creation).
    _CASCADE_MODE_ENUM.create(bind=bind, checkfirst=True)
    _REQUISITE_SOURCE_ENUM.create(bind=bind, checkfirst=True)
    _CASCADE_ATTEMPT_STATUS_ENUM.create(bind=bind, checkfirst=True)

    # --- existing-table column additions (defaults so back-fill is automatic) ---

    if not _has_column(inspector, "users", "is_system"):
        op.add_column(
            "users",
            sa.Column(
                "is_system",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    if not _has_column(inspector, "merchants", "cascade_mode"):
        op.add_column(
            "merchants",
            sa.Column(
                "cascade_mode",
                _CASCADE_MODE_ENUM,
                nullable=False,
                server_default="off",
            ),
        )

    if not _has_column(inspector, "requisites", "source"):
        op.add_column(
            "requisites",
            sa.Column(
                "source",
                _REQUISITE_SOURCE_ENUM,
                nullable=False,
                server_default="local",
            ),
        )

    # --- cascade_providers ---

    if not _has_table(inspector, "cascade_providers"):
        op.create_table(
            "cascade_providers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code", sa.String(length=64), nullable=False, unique=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("adapter_type", sa.String(length=64), nullable=False),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
            sa.Column("base_url", sa.String(length=512), nullable=False),
            sa.Column("api_key_encrypted", sa.String(length=1024), nullable=True),
            sa.Column("api_secret_encrypted", sa.String(length=1024), nullable=True),
            sa.Column("webhook_secret_encrypted", sa.String(length=1024), nullable=True),
            sa.Column(
                "virtual_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                unique=True,
            ),
            sa.Column(
                "virtual_trader_id",
                sa.Integer(),
                sa.ForeignKey("traders.id"),
                nullable=False,
                unique=True,
            ),
            sa.Column(
                "rates",
                sa.dialects.postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column(
                "fees",
                sa.dialects.postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            sa.Column("min_amount_fiat", sa.Numeric(15, 2), nullable=True),
            sa.Column("max_amount_fiat", sa.Numeric(15, 2), nullable=True),
            sa.Column(
                "cb_window_seconds",
                sa.Integer(),
                nullable=False,
                server_default="300",
            ),
            sa.Column(
                "cb_threshold_failures",
                sa.Integer(),
                nullable=False,
                server_default="5",
            ),
            sa.Column(
                "cb_threshold_rate",
                sa.Float(),
                nullable=False,
                server_default="0.5",
            ),
            sa.Column(
                "cb_cooldown_seconds",
                sa.Integer(),
                nullable=False,
                server_default="600",
            ),
            sa.Column("disabled_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "request_timeout_ms",
                sa.Integer(),
                nullable=False,
                server_default="3000",
            ),
            sa.Column(
                "cancel_timeout_ms",
                sa.Integer(),
                nullable=False,
                server_default="2000",
            ),
            sa.Column(
                "priority_weight",
                sa.Integer(),
                nullable=False,
                server_default="100",
            ),
            sa.Column(
                "settings",
                sa.dialects.postgresql.JSONB(),
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
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
            "ix_cascade_providers_code", "cascade_providers", ["code"], unique=True
        )

    # --- cascade_groups ---

    if not _has_table(inspector, "cascade_groups"):
        op.create_table(
            "cascade_groups",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("name", sa.String(length=100), nullable=False, unique=True),
            sa.Column("description", sa.String(length=255), nullable=True),
            sa.Column("tier", sa.Integer(), nullable=False, server_default="1"),
            sa.Column(
                "timeout_ms", sa.Integer(), nullable=False, server_default="3000"
            ),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
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
        op.create_index("ix_cascade_groups_tier", "cascade_groups", ["tier"])

    # --- cascade_group_providers ---

    if not _has_table(inspector, "cascade_group_providers"):
        op.create_table(
            "cascade_group_providers",
            sa.Column(
                "group_id",
                sa.Integer(),
                sa.ForeignKey("cascade_groups.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "provider_id",
                sa.Integer(),
                sa.ForeignKey("cascade_providers.id", ondelete="CASCADE"),
                primary_key=True,
            ),
        )

    # --- cascade_group_merchants ---

    if not _has_table(inspector, "cascade_group_merchants"):
        op.create_table(
            "cascade_group_merchants",
            sa.Column(
                "group_id",
                sa.Integer(),
                sa.ForeignKey("cascade_groups.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "merchant_id",
                sa.Integer(),
                sa.ForeignKey("merchants.id", ondelete="CASCADE"),
                primary_key=True,
            ),
        )
        op.create_index(
            "ix_cascade_group_merchants_merchant",
            "cascade_group_merchants",
            ["merchant_id"],
        )

    # --- cascade_order_attempts ---

    if not _has_table(inspector, "cascade_order_attempts"):
        op.create_table(
            "cascade_order_attempts",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "order_id",
                sa.Integer(),
                sa.ForeignKey("orders.id", ondelete="CASCADE"),
                nullable=True,
            ),
            sa.Column(
                "provider_id",
                sa.Integer(),
                sa.ForeignKey("cascade_providers.id"),
                nullable=False,
            ),
            sa.Column(
                "group_id",
                sa.Integer(),
                sa.ForeignKey("cascade_groups.id"),
                nullable=True,
            ),
            sa.Column("tier", sa.Integer(), nullable=True),
            sa.Column(
                "started_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("latency_ms", sa.Integer(), nullable=True),
            sa.Column(
                "status",
                _CASCADE_ATTEMPT_STATUS_ENUM,
                nullable=False,
                server_default="in_flight",
            ),
            sa.Column("refusal_reason", sa.String(length=255), nullable=True),
            sa.Column("error_code", sa.String(length=128), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("external_order_id", sa.String(length=255), nullable=True),
            sa.Column("requisite_snapshot", sa.dialects.postgresql.JSONB(), nullable=True),
            sa.Column(
                "requisite_id",
                sa.Integer(),
                sa.ForeignKey("requisites.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("provider_rate", sa.Numeric(15, 4), nullable=True),
            sa.Column("provider_fee_usdt", sa.Numeric(15, 4), nullable=True),
            sa.Column("our_profit_usdt", sa.Numeric(15, 4), nullable=True),
            sa.Column("idempotency_key", sa.String(length=64), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "provider_id",
                "external_order_id",
                name="uq_cascade_attempt_provider_external",
            ),
        )
        op.create_index(
            "ix_cascade_order_attempts_order_id",
            "cascade_order_attempts",
            ["order_id"],
        )
        op.create_index(
            "ix_cascade_order_attempts_provider_id",
            "cascade_order_attempts",
            ["provider_id"],
        )
        op.create_index(
            "ix_cascade_order_attempts_status",
            "cascade_order_attempts",
            ["status"],
        )
        op.create_index(
            "ix_cascade_order_attempts_external",
            "cascade_order_attempts",
            ["external_order_id"],
        )
        op.create_index(
            "ix_cascade_order_attempts_idempotency",
            "cascade_order_attempts",
            ["idempotency_key"],
        )

    # --- cascade_provider_metrics ---

    if not _has_table(inspector, "cascade_provider_metrics"):
        op.create_table(
            "cascade_provider_metrics",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "provider_id",
                sa.Integer(),
                sa.ForeignKey("cascade_providers.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("bucket_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "request_count", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "success_count", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "failure_count", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "timeout_count", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "cancel_count", sa.Integer(), nullable=False, server_default="0"
            ),
            sa.Column(
                "total_latency_ms",
                sa.Integer(),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "total_volume_usdt",
                sa.Numeric(20, 4),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "total_profit_usdt",
                sa.Numeric(20, 4),
                nullable=False,
                server_default="0",
            ),
            sa.UniqueConstraint(
                "provider_id", "bucket_at", name="uq_cascade_metric_bucket"
            ),
        )
        op.create_index(
            "ix_cascade_provider_metrics_bucket",
            "cascade_provider_metrics",
            ["bucket_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    for table in (
        "cascade_provider_metrics",
        "cascade_order_attempts",
        "cascade_group_merchants",
        "cascade_group_providers",
        "cascade_groups",
        "cascade_providers",
    ):
        if _has_table(inspector, table):
            op.drop_table(table)

    op.execute(sa.text("DROP TYPE IF EXISTS cascadeattemptstatus"))

    if _has_column(inspector, "requisites", "source"):
        op.drop_column("requisites", "source")
    op.execute(sa.text("DROP TYPE IF EXISTS requisitesource"))

    if _has_column(inspector, "merchants", "cascade_mode"):
        op.drop_column("merchants", "cascade_mode")
    op.execute(sa.text("DROP TYPE IF EXISTS merchantcascademode"))

    if _has_column(inspector, "users", "is_system"):
        op.drop_column("users", "is_system")
