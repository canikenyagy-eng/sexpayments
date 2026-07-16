"""payout terminals: dedicated payout-side terminal (own key/balance/rate)

Revision ID: 049
Revises: 048
Create Date: 2026-06-17

Payouts move OFF the payin merchant onto their own terminal:
  * payout_terminals — own api_key/secret, rate_config, commission_percent,
    ttl_minutes, receipts_to_close, min/max amount, status, webhook.
  * payout_terminal_traders — ACL: which traders may serve a terminal's pool.
  * balances.payout_terminal_id — terminals hold their OWN WORK/ESCROW balance
    (admin top-up funds payouts).
  * payouts.merchant_id → payout_terminal_id (repoint; idempotency now per
    (terminal, external_id)).
  * merchants.payout_commission_percent / payout_ttl_seconds — DROPPED (moved to
    the terminal).

The payout feature was never wired to any API/worker, so the payouts table holds
no real data — it is cleared before the repoint. Idempotent / re-appliable.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "049"
down_revision = "048"
branch_labels = None
depends_on = None

_PT_STATUS = postgresql.ENUM(
    "pending", "test", "enabled", "disabled", "blocked", "archived",
    name="payout_terminal_status",
    create_type=False,
)
_CURRENCY = postgresql.ENUM(name="currency", create_type=False)


def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def _has_column(inspector, table: str, column: str) -> bool:
    return _has_table(inspector, table) and any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _PT_STATUS.create(bind=bind, checkfirst=True)

    # --- payout_terminals ---
    if not _has_table(inspector, "payout_terminals"):
        op.create_table(
            "payout_terminals",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("status", _PT_STATUS, nullable=False, server_default="enabled"),
            sa.Column("currency", _CURRENCY, nullable=False, server_default="RUB"),
            sa.Column("api_key", sa.String(length=255), nullable=False),
            sa.Column("api_secret", sa.String(length=255), nullable=False),
            sa.Column("rate_config_id", sa.Integer(), sa.ForeignKey("rate_configs.id"), nullable=True),
            sa.Column("commission_percent", sa.Numeric(5, 2), nullable=False, server_default="0"),
            sa.Column("ttl_minutes", sa.Integer(), nullable=False, server_default="60"),
            sa.Column("receipts_to_close", sa.Integer(), nullable=False, server_default="1"),
            sa.Column("min_amount", sa.Numeric(15, 4), nullable=True),
            sa.Column("max_amount", sa.Numeric(15, 4), nullable=True),
            sa.Column("webhook_url", sa.String(length=255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        )
        op.create_index("ix_payout_terminals_api_key", "payout_terminals", ["api_key"], unique=True)

    # --- payout_terminal_traders (ACL) ---
    if not _has_table(inspector, "payout_terminal_traders"):
        op.create_table(
            "payout_terminal_traders",
            sa.Column("payout_terminal_id", sa.Integer(),
                      sa.ForeignKey("payout_terminals.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("trader_id", sa.Integer(),
                      sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        )

    # --- balances.payout_terminal_id ---
    if not _has_column(inspector, "balances", "payout_terminal_id"):
        op.execute(
            "ALTER TABLE balances ADD COLUMN payout_terminal_id INTEGER "
            "REFERENCES payout_terminals(id)"
        )
        op.execute("CREATE INDEX IF NOT EXISTS ix_balances_payout_terminal_id ON balances (payout_terminal_id)")
        op.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_payout_terminal_balance "
            "ON balances (payout_terminal_id, type, currency)"
        )

    # --- repoint payouts: merchant_id → payout_terminal_id ---
    # Guarded by the presence of the OLD column so a re-run never wipes live data:
    # the table held no real data (the feature was unwired), so we clear it once,
    # then swap the FK column.
    if _has_column(inspector, "payouts", "merchant_id"):
        op.execute("DELETE FROM payouts")
        op.execute("ALTER TABLE payouts DROP CONSTRAINT IF EXISTS uq_payout_merchant_external")
        op.execute("ALTER TABLE payouts DROP COLUMN merchant_id")
    if not _has_column(inspector, "payouts", "payout_terminal_id"):
        op.execute(
            "ALTER TABLE payouts ADD COLUMN payout_terminal_id INTEGER NOT NULL "
            "REFERENCES payout_terminals(id)"
        )
        op.execute("CREATE INDEX IF NOT EXISTS ix_payouts_payout_terminal_id ON payouts (payout_terminal_id)")
        op.execute(
            "ALTER TABLE payouts ADD CONSTRAINT uq_payout_terminal_external "
            "UNIQUE (payout_terminal_id, external_id)"
        )

    # --- drop merchant payout config (moved to terminal) ---
    for col in ("payout_commission_percent", "payout_ttl_seconds"):
        op.execute(f"ALTER TABLE merchants DROP COLUMN IF EXISTS {col}")


def downgrade() -> None:
    # Restore merchant payout config columns.
    op.execute(
        "ALTER TABLE merchants ADD COLUMN IF NOT EXISTS payout_commission_percent "
        "NUMERIC(5,2) NOT NULL DEFAULT 0"
    )
    op.execute(
        "ALTER TABLE merchants ADD COLUMN IF NOT EXISTS payout_ttl_seconds "
        "INTEGER NOT NULL DEFAULT 3600"
    )
    # Repoint payouts back to merchant_id (table is cleared either way).
    op.execute("DELETE FROM payouts")
    op.execute("ALTER TABLE payouts DROP CONSTRAINT IF EXISTS uq_payout_terminal_external")
    op.execute("ALTER TABLE payouts DROP COLUMN IF EXISTS payout_terminal_id")
    op.execute(
        "ALTER TABLE payouts ADD COLUMN IF NOT EXISTS merchant_id INTEGER NOT NULL "
        "REFERENCES merchants(id)"
    )
    op.execute("ALTER TABLE balances DROP COLUMN IF EXISTS payout_terminal_id")
    op.execute("DROP TABLE IF EXISTS payout_terminal_traders")
    op.execute("DROP TABLE IF EXISTS payout_terminals")
    bind = op.get_bind()
    _PT_STATUS.drop(bind=bind, checkfirst=True)
