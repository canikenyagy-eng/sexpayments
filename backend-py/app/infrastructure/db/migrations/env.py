import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.core.config import get_settings
from app.infrastructure.db.base import Base

from app.modules.audit.models import AuditLog
from app.infrastructure.outbox.models import OutboxEvent
from app.modules.users.models import User
from app.modules.merchants.models import Merchant
from app.modules.requisites.models import Requisite, RequisiteLimit
from app.modules.payments.models import PaymentOption
from app.modules.orders.models import Order, OrderStatusHistory
from app.modules.callbacks.models import CallbackAttempt
from app.modules.finance.models import Balance, LedgerEntry, WithdrawalRequest
from app.modules.payouts.models import Payout  # noqa: F401
from app.modules.disputes.models import Dispute
from app.modules.rates.models import RateConfig
from app.modules.traders.models import Trader, TraderGroup
from app.modules.stats.models import StatsSnapshot, MerchantStatsSnapshot
from app.modules.teamleaders.models import TeamleadLink  # noqa: F401
from app.modules.cascading.models import (  # noqa: F401
    CascadeProvider,
    CascadeGroup,
    CascadeOrderAttempt,
    CascadeProviderMetric,
    cascade_group_providers,
    cascade_group_merchants,
)
from app.modules.settings.models import PlatformSetting  # noqa: F401
from app.modules.receipts.models import ReceiptModeration  # noqa: F401
from app.modules.receipts.models import Receipt  # noqa: F401
from app.modules.clients.models import Client  # noqa: F401
from app.modules.achievements.models import (  # noqa: F401
    TraderAchievement,
    TraderDailyVolume,
)

# add your model's MetaData object here
target_metadata = Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def get_url():
    settings = get_settings()
    return str(settings.DATABASE_URL)


def run_migrations_offline() -> None:

    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
