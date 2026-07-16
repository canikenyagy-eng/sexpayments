"""Celery task: run a receipt verification asynchronously after a merchant
uploads a receipt.

The merchant-side request returns as soon as the file is persisted; this
task picks up the order, resolves the assigned trader, and — if the trader
has auto-check enabled and an active provider is configured — runs a
verification. Failure paths (no trader, no provider, insufficient funds)
are logged but never retried: auto-check is best-effort.
"""
import asyncio

from sqlalchemy import select

from app.common.enums.receipt_checks import ReceiptCheckTrigger
from app.core.exceptions import (
    ConflictException,
    NotFoundException,
    ValidationException,
)
from app.core.logging import get_logger
from app.infrastructure.db.session import WorkerSessionLocal as SessionLocal
from app.modules.orders.models import Order
from app.modules.receipt_checks.service import ReceiptCheckService
from app.modules.traders.models import Trader
from app.modules.users.models import User
from app.workers.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(
    name="app.workers.tasks.receipt_checks.run_auto_check_for_order",
    bind=True,
    max_retries=0,  # auto-check is best-effort — don't retry on adapter errors
)
def run_auto_check_for_order(self, order_id: int) -> None:
    async def _run() -> tuple[bool, int | None]:
        """Returns `(auto_check_was_on, completed_check_id)`. The outer
        wrapper uses this to decide whether to dispatch the trader-bot
        notification ourselves (when auto-check was the owner of that
        notification) and whether to include the verdict in it.
        """
        async with SessionLocal() as session:
            # WorkerSessionLocal has no implicit commit on exit, so we wrap
            # the entire unit of work in `session.begin()` FIRST — before
            # any other statement opens an implicit transaction that would
            # clash with the explicit `begin()`.
            check_id: int | None = None
            auto_on = False
            try:
                async with session.begin():
                    order = await session.get(Order, order_id)
                    if not order:
                        logger.warning("auto-check: order %s vanished", order_id)
                        return (False, None)
                    if not order.receipt_file:
                        logger.info("auto-check: order %s has no receipt yet", order_id)
                        return (False, None)
                    if not order.trader_id:
                        logger.info(
                            "auto-check: order %s has no assigned trader", order_id
                        )
                        return (False, None)

                    trader_user = await session.get(User, order.trader_id)
                    if not trader_user:
                        logger.warning(
                            "auto-check: trader user %s missing", order.trader_id
                        )
                        return (False, None)

                    trader_profile = (
                        await session.execute(
                            select(Trader).where(Trader.user_id == trader_user.id)
                        )
                    ).scalar_one_or_none()
                    if not trader_profile or not trader_profile.receipt_auto_check:
                        # Toggle is off — silent skip, confirm_order already
                        # sent the regular notification.
                        return (False, None)

                    auto_on = True
                    service = ReceiptCheckService(session)
                    # Auto-check runs with the trader's default provider
                    # (falls back to the first active one); never the global
                    # "active" singleton, which no longer exists.
                    provider = await service.resolve_provider_for_trader(
                        trader_profile, requested_id=None
                    )
                    if provider is None:
                        logger.info(
                            "auto-check: order %s has no active provider", order_id
                        )
                        return (auto_on, None)
                    check = await service.run_check_for_order(
                        order=order,
                        trader_user=trader_user,
                        trigger=ReceiptCheckTrigger.AUTO,
                        provider=provider,
                    )
                    check_id = check.id
            except (ConflictException, ValidationException, NotFoundException) as exc:
                # Auto-check was on but failed (no provider, no balance,
                # bad file, etc.). Trader still gets the bot notification —
                # just without a verdict line.
                logger.info("auto-check: order %s skipped: %s", order_id, exc)
            return (auto_on, check_id)

    try:
        auto_on, check_id = asyncio.run(_run())
    except Exception as exc:  # pragma: no cover — defensive
        logger.exception("auto-check failed for order %s: %s", order_id, exc)
        return

    # When the trader has auto-check on, `confirm_order` deliberately
    # skipped the immediate trader-bot notification so we could fold the
    # verdict into it. Now that the check is done (or known-skipped) we
    # dispatch the notification ourselves.
    if auto_on:
        try:
            celery_app.send_task(
                "app.workers.tasks.trader_bot.notify_trader_new_receipt",
                args=[order_id],
                kwargs={"receipt_check_id": check_id},
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "notify_trader_new_receipt enqueue failed (order_id=%s): %s",
                order_id, exc,
            )
