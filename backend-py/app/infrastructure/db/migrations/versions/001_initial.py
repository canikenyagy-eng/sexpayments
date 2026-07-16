"""create all tables from current models

Revision ID: 001
Revises:
Create Date: 2026-04-09

"""
from typing import Sequence, Union

from alembic import op
from app.infrastructure.db.base import Base

import app.modules.audit.models  # noqa: F401
import app.modules.callbacks.models  # noqa: F401
import app.modules.disputes.models  # noqa: F401
import app.modules.finance.models  # noqa: F401
import app.modules.merchants.models  # noqa: F401
import app.modules.orders.models  # noqa: F401
import app.modules.payments.models  # noqa: F401
import app.modules.rates.models  # noqa: F401
import app.modules.receipts.models  # noqa: F401
import app.modules.requisites.models  # noqa: F401
import app.modules.settings.models  # noqa: F401
import app.modules.stats.models  # noqa: F401
import app.modules.teamleaders.models  # noqa: F401
import app.modules.traders.models  # noqa: F401
import app.modules.users.models  # noqa: F401
import app.infrastructure.outbox.models  # noqa: F401

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
