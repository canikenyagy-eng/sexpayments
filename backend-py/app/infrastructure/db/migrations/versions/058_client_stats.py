"""clients rollup stats — deal counts + turnover

Revision ID: 058
Revises: 057

Adds the per-client aggregate columns shown on the admin Clients page:
``total_orders`` / ``successful_orders`` / ``turnover_usdt`` (Σ amount_usdt of
SUCCESS orders). They are recomputed off the hot path by ``refresh_clients_task``
(a throttled full GROUP-BY over ``orders``) — order creation never writes here.
Conversion (successful / total) is derived in the API, not stored. Idempotent
for re-runs on partially-applied schemas.
"""
import sqlalchemy as sa
from alembic import op

from app.infrastructure.db.migrations._helpers import has_column

revision = "058"
down_revision = "057"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("total_orders", sa.Column("total_orders", sa.Integer(), nullable=False, server_default=sa.text("0"))),
    ("successful_orders", sa.Column("successful_orders", sa.Integer(), nullable=False, server_default=sa.text("0"))),
    ("turnover_usdt", sa.Column("turnover_usdt", sa.Numeric(15, 4), nullable=False, server_default=sa.text("0"))),
)


def upgrade() -> None:
    bind = op.get_bind()
    for name, column in _COLUMNS:
        if not has_column(bind, "clients", name):
            op.add_column("clients", column)


def downgrade() -> None:
    bind = op.get_bind()
    for name, _ in _COLUMNS:
        if has_column(bind, "clients", name):
            op.drop_column("clients", name)
