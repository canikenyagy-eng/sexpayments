"""receipt-check module: providers, checks log, trader auto-check toggle

Revision ID: 030
Revises: 029
Create Date: 2026-05-11

Stays at 030 so existing DBs that already recorded ``version_num='030'``
(applied receipt-check tables) don't lose their alembic anchor. The
other 2026-05-10 branch (cascade_rate_source) was moved to 032 — the
two branches are independent, so ordering between them doesn't matter.

Adds the receipt-check (anti-fraud receipt verification) layer:
  * receipt_check_providers — third-party verification adapters (TREXO,
    future others). Single active provider at a time enforced in service
    layer (DB still allows multiple rows so admin can keep an inactive
    fallback configured).
  * receipt_checks — every verification attempt (manual, auto, cache-hit
    replay). Linked to order + the trader who initiated.
  * traders.receipt_auto_check — per-trader toggle to enable automatic
    verification when a merchant uploads a receipt.

Idempotent (same convention as 029): all create/alter operations check the
existing schema first so the migration can be re-applied across partially
upgraded environments.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "030"
down_revision = "029"
branch_labels = None
depends_on = None


_RECEIPT_CHECK_STATUS_ENUM = postgresql.ENUM(
    "pending",
    "success",
    "failed",
    "cached",
    name="receiptcheckstatus",
    create_type=False,
)


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def _has_table(inspector, name: str) -> bool:
    return name in inspector.get_table_names()


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _RECEIPT_CHECK_STATUS_ENUM.create(bind=bind, checkfirst=True)

    # --- traders.receipt_auto_check toggle ---

    if not _has_column(inspector, "traders", "receipt_auto_check"):
        op.add_column(
            "traders",
            sa.Column(
                "receipt_auto_check",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    # --- receipt_check_providers ---

    if not _has_table(inspector, "receipt_check_providers"):
        op.create_table(
            "receipt_check_providers",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("code", sa.String(length=64), nullable=False, unique=True),
            sa.Column("name", sa.String(length=255), nullable=False),
            sa.Column("adapter_type", sa.String(length=64), nullable=False),
            sa.Column(
                "is_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column("base_url", sa.String(length=512), nullable=False),
            sa.Column("api_key_encrypted", sa.String(length=1024), nullable=True),
            # Last 4 characters of the plaintext API key, captured at
            # creation/update so the admin UI can render `sk_live_***abcd`
            # without ever decrypting the secret. The full key is read-once
            # and never shown again.
            sa.Column("api_key_tail", sa.String(length=8), nullable=True),
            sa.Column(
                "price_usdt",
                sa.Numeric(15, 4),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "request_timeout_ms",
                sa.Integer(),
                nullable=False,
                server_default="90000",
            ),
            sa.Column(
                "settings",
                postgresql.JSONB(),
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
            "ix_receipt_check_providers_code",
            "receipt_check_providers",
            ["code"],
            unique=True,
        )
        op.create_index(
            "ix_receipt_check_providers_active",
            "receipt_check_providers",
            ["is_active"],
        )

    # --- receipt_checks ---

    if not _has_table(inspector, "receipt_checks"):
        op.create_table(
            "receipt_checks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "order_id",
                sa.Integer(),
                sa.ForeignKey("orders.id", ondelete="CASCADE"),
                nullable=False,
            ),
            # Provider may be nulled out later (provider deleted) but the
            # historic check row survives for audit. Same pattern as
            # ledger entries.
            sa.Column(
                "provider_id",
                sa.Integer(),
                sa.ForeignKey("receipt_check_providers.id", ondelete="SET NULL"),
                nullable=True,
            ),
            # Trader user that triggered or owns the check. NULL when the
            # check was initiated by the auto-pipeline before a trader is
            # assigned (kept nullable so the row still records the attempt).
            sa.Column(
                "trader_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "trigger",
                sa.String(length=16),
                nullable=False,
            ),  # manual | auto
            sa.Column(
                "status",
                _RECEIPT_CHECK_STATUS_ENUM,
                nullable=False,
                server_default="pending",
            ),
            sa.Column("file_path", sa.String(length=512), nullable=False),
            # SHA-256 hex of the file bytes. Used to detect "already checked"
            # — if a row exists with the same hash on the same order we
            # reuse its result instead of charging the trader again.
            sa.Column("file_sha256", sa.String(length=64), nullable=False),
            sa.Column("is_clean", sa.Boolean(), nullable=True),
            sa.Column(
                "verdict",
                postgresql.JSONB(),
                nullable=True,
            ),
            sa.Column(
                "parsed_data",
                postgresql.JSONB(),
                nullable=True,
            ),
            sa.Column("provider_check_id", sa.String(length=64), nullable=True),
            sa.Column("provider_tx_id", sa.String(length=128), nullable=True),
            sa.Column(
                "raw_response",
                postgresql.JSONB(),
                nullable=True,
            ),
            sa.Column("error_code", sa.String(length=64), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column(
                "price_usdt",
                sa.Numeric(15, 4),
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "charged",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "refunded",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index(
            "ix_receipt_checks_order_id",
            "receipt_checks",
            ["order_id"],
        )
        op.create_index(
            "ix_receipt_checks_trader_user_id",
            "receipt_checks",
            ["trader_user_id"],
        )
        op.create_index(
            "ix_receipt_checks_file_sha256",
            "receipt_checks",
            ["file_sha256"],
        )
        op.create_index(
            "ix_receipt_checks_status",
            "receipt_checks",
            ["status"],
        )

    # --- extend ledgerreferencetype enum with new values ---
    # Postgres requires ALTER TYPE ... ADD VALUE outside of a transaction
    # block, but Alembic runs in a tx by default. We use the standard
    # workaround: explicit autocommit_block().
    #
    # The DB enum was provisioned by migration 001 via Base.metadata.create_all,
    # which stores SQLAlchemy Python *names* (uppercase identifiers) — so we
    # add UPPERCASE labels to match what the ORM emits.
    new_values = ("RECEIPT_CHECK", "RECEIPT_CHECK_REFUND")
    existing = {
        r[0]
        for r in bind.execute(
            sa.text(
                """
                SELECT e.enumlabel
                FROM pg_type t
                JOIN pg_enum e ON e.enumtypid = t.oid
                WHERE t.typname = 'ledgerreferencetype'
                """
            )
        ).all()
    }
    with op.get_context().autocommit_block():
        for val in new_values:
            if val not in existing:
                op.execute(
                    sa.text(
                        f"ALTER TYPE ledgerreferencetype ADD VALUE IF NOT EXISTS '{val}'"
                    )
                )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_table(inspector, "receipt_checks"):
        op.drop_table("receipt_checks")

    if _has_table(inspector, "receipt_check_providers"):
        op.drop_table("receipt_check_providers")

    op.execute(sa.text("DROP TYPE IF EXISTS receiptcheckstatus"))

    if _has_column(inspector, "traders", "receipt_auto_check"):
        op.drop_column("traders", "receipt_auto_check")

    # Note: removing enum values from ledgerreferencetype requires
    # recreating the type and migrating existing rows. We intentionally
    # leave the extra values in place on downgrade — they are harmless.
