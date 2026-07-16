"""disputereason: add the CHECK_SUSPENDED label

The trader/admin PDF-video proof-request opens a dispute with
``DisputeReason.CHECK_SUSPENDED``. ``Dispute.reason`` is
``Column(Enum(DisputeReason))`` with NO ``values_callable``, so SQLAlchemy stores
the enum NAME — the Postgres enum type ``disputereason`` needs the label
``CHECK_SUSPENDED`` (uppercase), otherwise the INSERT fails with
``invalid input value for enum disputereason: "CHECK_SUSPENDED"``.

Revision ID: 065
Revises: 064
Create Date: 2026-07-05
"""
from alembic import op

from app.infrastructure.db.migrations._helpers import enum_exists, has_enum_value

revision = "065"
down_revision = "064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if enum_exists(bind, "disputereason") and not has_enum_value(
        bind, "disputereason", "CHECK_SUSPENDED"
    ):
        # ALTER TYPE ... ADD VALUE must run outside a transaction block.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE disputereason ADD VALUE IF NOT EXISTS 'CHECK_SUSPENDED'")


def downgrade() -> None:
    # Postgres has no DROP VALUE for enum labels; leaving CHECK_SUSPENDED is
    # harmless (nothing else references it after the feature is rolled back).
    pass
