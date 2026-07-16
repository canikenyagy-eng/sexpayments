"""trader default receipt-check provider

Revision ID: 060
Revises: 059
Create Date: 2026-07-03

Adds ``traders.default_receipt_check_provider_id`` — the trader's preferred
receipt-check provider, pre-selected in the manual "check receipt" modal and
used for automatic checks. Nullable FK to ``receipt_check_providers`` with
``ON DELETE SET NULL`` so deleting a provider clears any trader's default
instead of dangling.

Idempotent (same convention as 030): the add/index operations check the
existing schema first so the migration can be re-applied across partially
upgraded environments.
"""
from alembic import op
import sqlalchemy as sa


revision = "060"
down_revision = "059"
branch_labels = None
depends_on = None


def _has_column(inspector, table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspector.get_columns(table))


def _has_fk(inspector, table: str, name: str) -> bool:
    return any(fk.get("name") == name for fk in inspector.get_foreign_keys(table))


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not _has_column(inspector, "traders", "default_receipt_check_provider_id"):
        op.add_column(
            "traders",
            sa.Column(
                "default_receipt_check_provider_id",
                sa.Integer(),
                nullable=True,
            ),
        )
        op.create_foreign_key(
            "fk_traders_default_receipt_check_provider",
            "traders",
            "receipt_check_providers",
            ["default_receipt_check_provider_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if _has_fk(inspector, "traders", "fk_traders_default_receipt_check_provider"):
        op.drop_constraint(
            "fk_traders_default_receipt_check_provider",
            "traders",
            type_="foreignkey",
        )
    if _has_column(inspector, "traders", "default_receipt_check_provider_id"):
        op.drop_column("traders", "default_receipt_check_provider_id")
