"""add source column to orders

Revision ID: 012
Revises: 011

Made idempotent for re-runs on partially-applied schemas.
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.infrastructure.db.migrations._helpers import (
    enum_exists,
    has_column,
)

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    if not enum_exists(bind, "ordersource"):
        op.execute("CREATE TYPE ordersource AS ENUM ('api', 'web', 'bot')")

    if not has_column(bind, "orders", "source"):
        op.add_column(
            "orders",
            sa.Column(
                "source",
                postgresql.ENUM(
                    "api", "web", "bot",
                    name="ordersource",
                    create_type=False,
                ),
                nullable=False,
                server_default="api",
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()

    if has_column(bind, "orders", "source"):
        op.drop_column("orders", "source")

    if enum_exists(bind, "ordersource"):
        op.execute("DROP TYPE ordersource")
