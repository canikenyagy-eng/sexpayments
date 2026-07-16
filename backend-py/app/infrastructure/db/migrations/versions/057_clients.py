"""clients table — unique merchant clients (by merchant userId) + ban flag

Revision ID: 057
Revises: 056

A "client" is a unique ``(merchant_id, client_user_id)`` pair, where
``client_user_id`` is the merchant-supplied id (payin ``clientID`` / legacy
``userId``, already stored on ``orders.client_user_id``). The table is
MATERIALISED off the hot path by ``refresh_clients_task`` (rolls up
``orders.client_user_id``) — order creation never writes here. ``is_blocked``
is toggled only by the admin block/unblock action; a partial index keeps the
"rebuild the blocked set" query (Redis reconcile) touching only blocked rows.
Idempotent for re-runs on partially-applied schemas.
"""
import sqlalchemy as sa
from alembic import op

from app.infrastructure.db.migrations._helpers import has_index, has_table

revision = "057"
down_revision = "056"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_table(bind, "clients"):
        op.create_table(
            "clients",
            sa.Column("id", sa.BigInteger(), primary_key=True),
            sa.Column("merchant_id", sa.Integer(), sa.ForeignKey("merchants.id"), nullable=False),
            sa.Column("client_user_id", sa.String(length=255), nullable=False),
            sa.Column("is_blocked", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("block_reason", sa.String(length=500), nullable=True),
            sa.Column("blocked_by_admin_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("merchant_id", "client_user_id", name="uq_clients_merchant_client"),
        )

    # List page: filter by merchant, order by recency.
    if not has_index(bind, "clients", "ix_clients_merchant_last_seen"):
        op.create_index(
            "ix_clients_merchant_last_seen",
            "clients",
            ["merchant_id", "last_seen_at"],
        )

    # Blocked-set reconcile (Redis rebuild) scans ONLY blocked rows.
    if not has_index(bind, "clients", "ix_clients_blocked"):
        op.create_index(
            "ix_clients_blocked",
            "clients",
            ["merchant_id", "client_user_id"],
            postgresql_where=sa.text("is_blocked"),
        )


def downgrade() -> None:
    bind = op.get_bind()
    if has_index(bind, "clients", "ix_clients_blocked"):
        op.drop_index("ix_clients_blocked", table_name="clients")
    if has_index(bind, "clients", "ix_clients_merchant_last_seen"):
        op.drop_index("ix_clients_merchant_last_seen", table_name="clients")
    if has_table(bind, "clients"):
        op.drop_table("clients")
