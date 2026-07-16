"""cascade_providers: add rate_source + rate_config_id

Revision ID: 032
Revises: 031
Create Date: 2026-05-10 (slotted in as 032 after the receipt-check pair)

Originally created as a sibling of 030 (receipt_checks) on the 2026-05-10
branch — both pointed at parent 029, giving Alembic two heads with the
same id. To resolve, this branch was moved to the tail of the chain so
the receipt-check 030/031 nodes stayed put (matching deployments that
had already recorded them in ``alembic_version``). The two branches are
independent so this reordering doesn't change semantics.

Adds the rate-source toggle to each cascade provider:
  * rate_source     — enum cascaderatesource ('provider' / 'platform')
  * rate_config_id  — FK -> rate_configs.id, used when rate_source='platform'

Existing rows default to 'provider' (current behaviour: use the adapter's
quoted rate). The toggle is set per-provider from the admin UI.

Idempotent — safe to re-run on top of partial application.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "032"
down_revision = "031"
branch_labels = None
depends_on = None


_RATE_SOURCE_ENUM = postgresql.ENUM(
    "provider",
    "platform",
    name="cascaderatesource",
    create_type=False,
)


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    _RATE_SOURCE_ENUM.create(bind=bind, checkfirst=True)

    if not _has_column(inspector, "cascade_providers", "rate_source"):
        op.add_column(
            "cascade_providers",
            sa.Column(
                "rate_source",
                _RATE_SOURCE_ENUM,
                nullable=False,
                server_default="provider",
            ),
        )

    if not _has_column(inspector, "cascade_providers", "rate_config_id"):
        op.add_column(
            "cascade_providers",
            sa.Column(
                "rate_config_id",
                sa.Integer(),
                sa.ForeignKey("rate_configs.id"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_column(inspector, "cascade_providers", "rate_config_id"):
        op.drop_column("cascade_providers", "rate_config_id")

    if _has_column(inspector, "cascade_providers", "rate_source"):
        op.drop_column("cascade_providers", "rate_source")

    op.execute(sa.text("DROP TYPE IF EXISTS cascaderatesource"))
