"""receipts table — unified multi-receipt store

Revision ID: 046
Revises: 045
Create Date: 2026-06-13

ONE table holding every receipt/proof file. A receipt always belongs to an
order (order_id) and is optionally attached to a dispute (dispute_id, appeal
evidence). One order → many receipts. Per-receipt moderation_status.

Backfills one row per existing ``orders.receipt_file`` so nothing is lost; the
legacy single column is kept as a mirror of the latest receipt and stays
populated by the upload paths, so existing single-file readers keep working.

Idempotent: guarded CREATE TYPE / CREATE TABLE IF NOT EXISTS / IF NOT EXISTS
indexes / NOT EXISTS backfill.
"""
from alembic import op

revision = "046"
down_revision = "045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # gen_random_uuid() for the backfill — core in PG13+, pgcrypto otherwise.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    # receiptsource enum (CREATE TYPE has no IF NOT EXISTS — guard it).
    op.execute(
        """
        DO $$ BEGIN
            CREATE TYPE receiptsource AS ENUM
                ('merchant_api','merchant_web','dispute_bot','system','trader');
        EXCEPTION WHEN duplicate_object THEN NULL; END $$;
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS receipts (
            id                SERIAL PRIMARY KEY,
            uuid              UUID NOT NULL,
            order_id          INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
            dispute_id        INTEGER REFERENCES disputes(id) ON DELETE SET NULL,
            file_path         VARCHAR(255) NOT NULL,
            sha256            VARCHAR(64),
            file_size         INTEGER,
            mime              VARCHAR(100),
            source            receiptsource NOT NULL DEFAULT 'merchant_api',
            uploaded_by       VARCHAR(50),
            moderation_status moderationstatus NOT NULL DEFAULT 'none',
            created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_receipts_uuid ON receipts(uuid)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_receipts_order_id ON receipts(order_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_receipts_dispute_id ON receipts(dispute_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_receipts_sha256 ON receipts(sha256)")

    # Backfill one receipt per existing order that has a receipt_file, mirroring
    # the legacy single-file columns. Skip orders that already have a receipt row
    # (re-run safe).
    op.execute(
        """
        INSERT INTO receipts
            (uuid, order_id, file_path, source, uploaded_by, moderation_status, created_at)
        SELECT gen_random_uuid(), o.id, o.receipt_file,
               'merchant_api'::receiptsource,
               COALESCE(o.receipt_uploaded_by, 'merchant'),
               o.moderation_status,
               COALESCE(o.receipt_uploaded_at, o.created_at)
        FROM orders o
        WHERE o.receipt_file IS NOT NULL AND o.receipt_file <> ''
          AND NOT EXISTS (SELECT 1 FROM receipts r WHERE r.order_id = o.id)
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS receipts")
    op.execute("DROP TYPE IF EXISTS receiptsource")
