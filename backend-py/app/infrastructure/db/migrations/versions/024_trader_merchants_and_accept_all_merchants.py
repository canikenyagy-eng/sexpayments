"""trader_merchants and traders.accept_all_merchants

Revision ID: 024
Revises: 023
Create Date: 2026-05-05

Idempotent: migration 001 uses ``Base.metadata.create_all(checkfirst=True)`` which
already provisions ``trader_merchants`` and ``traders.accept_all_merchants`` on a
fresh database (since both are now defined on the ORM models). Older databases
bootstrapped before these models existed need this migration to add them. We
detect what's already present and only create what's missing.
"""
from alembic import op
import sqlalchemy as sa


revision = "024"
down_revision = "023"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def _has_index(inspector, table: str, index: str) -> bool:
    return any(ix["name"] == index for ix in inspector.get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "traders", "accept_all_merchants"):
        op.add_column(
            "traders",
            sa.Column(
                "accept_all_merchants",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    if "trader_merchants" not in inspector.get_table_names():
        op.create_table(
            "trader_merchants",
            sa.Column(
                "trader_id",
                sa.Integer(),
                sa.ForeignKey("traders.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "merchant_id",
                sa.Integer(),
                sa.ForeignKey("merchants.id", ondelete="CASCADE"),
                primary_key=True,
            ),
        )

    # Refresh inspector — table may have been just created above.
    inspector = sa.inspect(bind)
    if "trader_merchants" in inspector.get_table_names() and not _has_index(
        inspector, "trader_merchants", "ix_trader_merchants_merchant_id"
    ):
        op.create_index(
            "ix_trader_merchants_merchant_id",
            "trader_merchants",
            ["merchant_id"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "trader_merchants" in inspector.get_table_names():
        if _has_index(inspector, "trader_merchants", "ix_trader_merchants_merchant_id"):
            op.drop_index("ix_trader_merchants_merchant_id", table_name="trader_merchants")
        op.drop_table("trader_merchants")

    if _has_column(inspector, "traders", "accept_all_merchants"):
        op.drop_column("traders", "accept_all_merchants")
