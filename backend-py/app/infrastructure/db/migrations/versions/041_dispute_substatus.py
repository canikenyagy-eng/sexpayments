"""disputes.substatus (premoderation context)

Revision ID: 041
Revises: 040
Create Date: 2026-06-05

Premoderation can now pull an active order into a dispute (e.g. the admin
asked the merchant for a PDF / video of the receipt). The dispute uses the
ordinary OPEN → RESOLVED/REJECTED lifecycle; ``substatus`` carries the extra
"what was requested" context so the UI and merchant know what's expected.

NULL substatus = an ordinary dispute (merchant-initiated, no premoderation
context). Idempotent: enum + column created IF NOT EXISTS.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "041"
down_revision = "040"
branch_labels = None
depends_on = None


_SUBSTATUS = postgresql.ENUM(
    "pdf_requested",
    "video_requested",
    name="disputesubstatus",
    create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    _SUBSTATUS.create(bind=bind, checkfirst=True)
    op.execute(
        "ALTER TABLE disputes "
        "ADD COLUMN IF NOT EXISTS substatus disputesubstatus"
    )
    # «PREMODERATION» reason добавляем в enum disputereason. ВАЖНО: значение
    # UPPERCASE — migration 040 пересоздала disputereason с label'ами =
    # member NAMES (UNKNOWN/HAS_PAYMENT/...), а Dispute.reason использует
    # SQLAlchemy-default Enum (по именам). lowercase здесь сломал бы вставку.
    op.execute(
        "ALTER TYPE disputereason ADD VALUE IF NOT EXISTS 'PREMODERATION'"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE disputes DROP COLUMN IF EXISTS substatus")
    op.execute("DROP TYPE IF EXISTS disputesubstatus")
    # disputereason 'premoderation' не удаляем — Postgres не умеет DROP VALUE.
