"""receipt_checks: unique index on (order_id, file_sha256) for LIVE checks

Closes the receipt-check double-charge race. Two trigger channels (manual
"Проверить чек" + auto-on-upload) could both pass the dedup read — which only
sees FINISHED (SUCCESS/CACHED) checks — and both create a PENDING row and charge
the trader's WORK balance. A partial unique index over LIVE
(pending/success/cached) checks makes the second INSERT conflict; the service
catches it and replays the winner instead of charging twice.

FAILED / refunded checks are excluded from the index so a re-check after a
failure is still allowed.

Deploy-safe: demotes any pre-existing duplicate LIVE checks (keeps the newest)
before building the index, so it can't fail on existing data. Historical
double-charges are NOT reversed here (manual reconciliation). No-op on SQLite
(partial indexes differ; the unit tests exercise the IntegrityError code path).
"""
import sqlalchemy as sa
from alembic import op

revision = "054"
down_revision = "053"
branch_labels = None
depends_on = None

_IDX = "uq_receipt_check_active_file"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    # 1. Demote older duplicate LIVE checks so the unique index can be built.
    op.execute(sa.text("""
        UPDATE receipt_checks
        SET status = 'failed',
            error_code = COALESCE(error_code, 'superseded_duplicate'),
            error_message = COALESCE(error_message,
                'superseded by a newer check (dedup for the live-check unique index)')
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (
                    PARTITION BY order_id, file_sha256 ORDER BY id DESC
                ) AS rn
                FROM receipt_checks
                WHERE status IN ('pending', 'success', 'cached')
            ) t WHERE t.rn > 1
        )
    """))
    # 2. At most ONE live check per (order_id, file_sha256).
    op.execute(sa.text(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS {_IDX}
        ON receipt_checks (order_id, file_sha256)
        WHERE status IN ('pending', 'success', 'cached')
    """))


def downgrade() -> None:
    op.execute(sa.text(f"DROP INDEX IF EXISTS {_IDX}"))
