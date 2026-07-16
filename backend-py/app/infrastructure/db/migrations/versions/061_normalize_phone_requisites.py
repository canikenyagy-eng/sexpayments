"""normalise phone requisites (sbp/sim) account_number to +7XXXXXXXXXX

Revision ID: 061
Revises: 060

Back-fills existing SBP/SIM requisites so the stored phone matches the canonical
form new writes now produce (see ``app.common.phone.normalize_phone``). The rule
is inlined here — migrations must be self-contained — and lenient: 10/11-digit RU
numbers become ``+7…``; anything unrecognisable is left untouched. Card
requisites are not touched.
"""
import re

import sqlalchemy as sa
from alembic import op

revision = "061"
down_revision = "060"
branch_labels = None
depends_on = None


def _normalize(raw):
    if not raw:
        return raw
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in ("7", "8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    else:
        return raw
    return "+" + digits


def upgrade() -> None:
    bind = op.get_bind()
    # UPPER(...) matches whether the enum is stored by NAME ('SBP') or value ('sbp').
    rows = bind.execute(
        sa.text(
            "SELECT id, account_number FROM requisites "
            "WHERE UPPER(payment_method::text) IN ('SBP', 'SIM')"
        )
    ).fetchall()
    for rid, account_number in rows:
        normalized = _normalize(account_number)
        if normalized != account_number:
            bind.execute(
                sa.text("UPDATE requisites SET account_number = :acc WHERE id = :id"),
                {"acc": normalized, "id": rid},
            )


def downgrade() -> None:
    # Irreversible: the original raw strings aren't retained.
    pass
