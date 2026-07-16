import asyncio
import logging
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, List, Optional, Sequence

from fastapi import UploadFile
from sqlalchemy import func, select, update
from sqlalchemy.orm import joinedload, raiseload, selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.orders.schemas import AdminOrderUpdate, MerchantPayinCreate, OrderDebugResponse
from app.common.enums.cascading import CascadeMode
from app.common.enums.orders import (
    ACTIVE_STATUSES,
    CANCELLABLE_STATUSES,
    COMPLETABLE_STATUSES,
    FAILABLE_STATUSES,
    RECEIPT_UPLOADABLE_STATUSES,
    OrderSource,
    OrderStatus,
)
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.receipt_moderations import ModerationStatus
from app.common.enums.receipts import ReceiptUploader
from app.common.enums.pooling import PoolingStrategy
from app.core.config import get_settings
from app.core.context import payin_snapshot_var
from app.core.exceptions import ConflictException, ForbiddenException, NotFoundException, ValidationException
from app.common.types import utcnow
from app.infrastructure.cache.redis import redis_client
from app.modules.base.service import BaseService
from app.modules.clients import block_cache
from app.modules.users.models import User
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order, OrderStatusHistory
from app.modules.orders.repository import OrderRepository
from app.modules.payments.models import PaymentOption
from app.modules.pooling.service import PoolingService
from app.modules.requisites.models import Requisite
from app.modules.requisites.repository import RequisiteLimitRepository
from app.workers.celery_app import celery_app
from app.modules.finance.models import LedgerEntry
from app.modules.callbacks.models import CallbackAttempt
from app.modules.disputes.models import Dispute
from app.modules.rates.service import RateService

settings = get_settings()
logger = logging.getLogger(__name__)


_TRACE_PAYIN = os.getenv("TRACE_PAYIN_TIMING", "0") == "1"


class _PayinTrace:
    """Lightweight stage-by-stage timing for create_payin_order.

    Active only when ``TRACE_PAYIN_TIMING=1`` is set in the environment —
    in production this is a no-op so the hot path stays free of overhead.
    Each ``stage()`` call logs the delta since the previous stage and the
    cumulative time since trace start.
    """

    __slots__ = ("_enabled", "_t0", "_last", "_label", "_stages")

    def __init__(self, label: str):
        self._enabled = _TRACE_PAYIN
        if not self._enabled:
            return
        self._t0 = time.perf_counter()
        self._last = self._t0
        self._label = label
        self._stages: list[tuple[str, float]] = []

    def stage(self, name: str) -> None:
        if not self._enabled:
            return
        now = time.perf_counter()
        dt_ms = (now - self._last) * 1000
        self._stages.append((name, dt_ms))
        self._last = now

    def flush(self) -> None:
        if not self._enabled:
            return
        total_ms = (time.perf_counter() - self._t0) * 1000
        breakdown = " | ".join(f"{n}={ms:.1f}ms" for n, ms in self._stages)
        logger.info("payin_trace[%s] total=%.1fms %s", self._label, total_ms, breakdown)


def _cascade_enabled(merchant) -> bool:
    """True iff the merchant's cascade_mode is GROUPED or POOLED.

    Defensive against mocks/legacy rows missing the column — anything that
    isn't a real CascadeMode enum is treated as "off".
    """
    mode = getattr(merchant, "cascade_mode", None)
    value = getattr(mode, "value", mode)
    return value in (CascadeMode.GROUPED.value, CascadeMode.POOLED.value)


# Order state machine — the ONLY legal status transitions. ``change_status``
# validates every move against this graph (single source of transition
# legality). Disputes open from any FINAL status (→ DISPUTED); the assignment
# freeze (CREATED→PENDING), the receipt-upload step (PENDING→RECEIPT_UPLOADED),
# the terminal settlements and the dispute decisions all live here.
_ALLOWED_TRANSITIONS: dict = {
    OrderStatus.CREATED: frozenset({OrderStatus.PENDING, OrderStatus.FAILED, OrderStatus.CANCELED}),
    OrderStatus.PENDING: frozenset({
        OrderStatus.RECEIPT_UPLOADED, OrderStatus.SUCCESS, OrderStatus.FAILED,
        OrderStatus.CANCELED, OrderStatus.DISPUTED,
    }),
    OrderStatus.RECEIPT_UPLOADED: frozenset({
        OrderStatus.SUCCESS, OrderStatus.FAILED, OrderStatus.CANCELED, OrderStatus.DISPUTED,
    }),
    OrderStatus.DISPUTED: frozenset({OrderStatus.SUCCESS, OrderStatus.FAILED, OrderStatus.CANCELED}),
    OrderStatus.SUCCESS: frozenset({OrderStatus.DISPUTED, OrderStatus.REFUNDED}),
    OrderStatus.FAILED: frozenset({OrderStatus.DISPUTED}),
    OrderStatus.CANCELED: frozenset({OrderStatus.DISPUTED}),
}

# Target statuses whose transition moves money — everything except CREATED and
# the status-only RECEIPT_UPLOADED step. ``change_status`` only resolves the
# trader/merchant + FinanceService for these (keeps the non-financial path lean).
_FINANCE_STATUSES = frozenset({
    OrderStatus.PENDING, OrderStatus.SUCCESS, OrderStatus.FAILED,
    OrderStatus.CANCELED, OrderStatus.DISPUTED, OrderStatus.REFUNDED,
})

# Settled / released terminal statuses. A manual ``force`` override between two
# of these first normalises the books back to the frozen state (reconcile) so
# the exit settlement is correct — see ``change_status``. REFUNDED is a
# released terminal too: it unwinds the order's money exactly like CANCELED
# (collateral back to the trader), so a SUCCESS→REFUNDED force reverses the full
# settlement first, then releases — "as if the order never happened".
_TERMINAL_STATUSES = frozenset({
    OrderStatus.SUCCESS, OrderStatus.FAILED, OrderStatus.CANCELED, OrderStatus.REFUNDED,
})


class OrderService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = OrderRepository(session)

    # ────────────────────────────────────────────────────────────
    # Atomic limit reservation for LOCAL requisites
    # ────────────────────────────────────────────────────────────

    async def _atomic_claim_requisite_capacity(
        self,
        requisite_id: int,
        amount: Decimal,
        *,
        lock_timeout_ms: int = 2000,
    ) -> None:
        """FOR UPDATE-lock + re-check лимита на конкретном requisite_id.

        Должно вызываться ВНУТРИ ``async with self.session.begin_nested()``
        (или другой явной транзакции) — иначе ``SET LOCAL`` и FOR UPDATE
        ничего не дают.

        Args:
            requisite_id: ID выбранного pooling'ом LOCAL-реквизита.
            amount: сумма ордера в фиате.
            lock_timeout_ms: сколько ждать освобождения лока перед fail-fast'ом.

        Raises:
            NotFoundException: лимит не помещается / lock_timeout / реквизит
                исчез между pooling и сейчас. Клиент видит то же что при
                пустом пуле — может ретраить или сразу 404.
        """
        from sqlalchemy import text
        from sqlalchemy.exc import DBAPIError, OperationalError

        from app.modules.requisites.models import RequisiteLimit

        _ACTIVE = (
            OrderStatus.PENDING,
            OrderStatus.RECEIPT_UPLOADED,
            OrderStatus.DISPUTED,
        )

        await self.session.execute(
            text(f"SET LOCAL lock_timeout = '{int(lock_timeout_ms)}ms'")
        )

        try:
            limit_row = (await self.session.execute(
                select(RequisiteLimit)
                .where(RequisiteLimit.requisite_id == requisite_id)
                .with_for_update()
            )).scalar_one_or_none()
        except (OperationalError, DBAPIError) as exc:
            # asyncpg.exceptions.LockNotAvailableError (PG SQLSTATE 55P03)
            msg = str(exc).lower()
            if "lock_not_available" in msg or "55p03" in msg or "lock timeout" in msg:
                logger.warning(
                    "requisite_limit_lock_timeout",
                    extra={"requisite_id": requisite_id, "amount": str(amount)},
                )
                raise NotFoundException(
                    "Requisite is busy serving another order; pooling will retry."
                )
            raise

        if limit_row is None:
            raise NotFoundException("Selected requisite is no longer available.")

        pending = (await self.session.execute(
            select(func.coalesce(func.sum(Order.amount), Decimal("0")))
            .where(
                Order.requisite_id == requisite_id,
                Order.status.in_(_ACTIVE),
            )
        )).scalar_one()
        pending = Decimal(str(pending or 0))
        amt = Decimal(str(amount))

        if (limit_row.current_daily_turnover + pending + amt) > limit_row.limit_daily:
            logger.info(
                "requisite_limit_daily_would_exceed",
                extra={
                    "requisite_id": requisite_id,
                    "current_daily": str(limit_row.current_daily_turnover),
                    "pending": str(pending),
                    "amount": str(amt),
                    "limit_daily": str(limit_row.limit_daily),
                },
            )
            raise NotFoundException(
                "Daily limit would be exceeded; pooling will retry."
            )
        if (limit_row.current_monthly_turnover + pending + amt) > limit_row.limit_monthly:
            raise NotFoundException(
                "Monthly limit would be exceeded; pooling will retry."
            )

        if limit_row.limit_max_concurrent_orders is not None:
            active_count = (await self.session.execute(
                select(func.count(Order.id))
                .where(
                    Order.requisite_id == requisite_id,
                    Order.status.in_(_ACTIVE),
                )
            )).scalar_one()
            if active_count >= limit_row.limit_max_concurrent_orders:
                raise NotFoundException(
                    "Concurrent orders limit would be exceeded."
                )

    async def _reload_order(self, order_id: int) -> Order:
        """Re-fetch the order with the requisite + payment_option the API response
        needs, in a SINGLE query.

        ``Order`` has three ``lazy="selectin"`` relationships — ``requisite``,
        ``payment_option`` and ``merchant`` — so a naive re-select fans out to
        multiple queries (and ``Requisite`` itself has three more selectins:
        limits / trader / payment_option). We collapse that:
          * ``joinedload`` pulls requisite + payment_option in the SAME query
            (both many-to-one, no row multiplication);
          * ``raiseload("*")`` on the requisite suppresses ITS selectins;
          * the top-level ``raiseload("*")`` suppresses ``Order.merchant`` — the
            response takes the merchant as a separate argument, never via
            ``order.merchant``.
        Net: 1 query instead of ~6. Serialized output is unchanged.

        ``populate_existing=True`` forces an identity-map refresh even though
        ``expire_on_commit=False`` leaves objects unexpired after begin_nested.
        """
        result = await self.session.execute(
            select(Order)
            .options(
                joinedload(Order.requisite).raiseload("*"),
                joinedload(Order.payment_option),
                raiseload("*"),
            )
            .where(Order.id == order_id)
            .execution_options(populate_existing=True)
        )
        return result.scalars().first()

    async def _calculate_trader_fee(self, trader_id: int, payment_method: str, amount_usdt: Decimal) -> Decimal:
        from app.modules.traders.models import Trader
        stmt = (
            select(Trader)
            .where(Trader.user_id == trader_id)
            .options(selectinload(Trader.method_configs), raiseload("*"))
        )
        result = await self.session.execute(stmt)
        trader_profile = result.scalar_one_or_none()

        if trader_profile and trader_profile.method_configs:
            method_conf = next(
                (mc for mc in trader_profile.method_configs
                 if mc.payment_method and mc.payment_method.value == payment_method),
                None,
            )
            if method_conf:
                from app.modules.settings.primetime import get_active_points
                # Base method fee + platform-wide PrimeTime + this trader's
                # materialized achievements bonus (percentage points). The bonus
                # column is loaded with the trader (no extra query) and recomputed
                # off the hot path by the achievements worker; default 0.
                fee_percent = (
                    Decimal(str(method_conf.fee))
                    + await get_active_points()
                    + Decimal(str(trader_profile.achievement_bonus_percent or 0))
                )
                return (amount_usdt * (fee_percent / Decimal("100"))).quantize(Decimal("0.0000"))
        return Decimal("0.0000")

    async def _resolve_trader_fee(
        self,
        *,
        found_requisite,
        cascade_result,
        payment_method: str,
        amount_usdt: Decimal,
    ) -> Decimal:
        """Trader reward stamped on the order's ``trader_fee_usdt``.

        For a **cascade-routed** order the "trader" is the provider's virtual
        trader; the platform's real cost is the provider's fee, not whatever
        the virtual trader happens to have configured (and primetime points
        must not inflate it). Stamping ``provider_fee_usdt`` here makes
        ``provider.fees`` the single source of truth: the ledger credits the
        virtual trader exactly the provider cost, and dashboard profit
        (``fee_usdt − trader_fee_usdt``) equals the real margin
        (``merchant_fee − provider_fee`` == ``CascadeOrderAttempt.our_profit``).

        **Local** orders keep using the trader's own configured fee. A
        cascade_result that did not win is not cascade-routed (the requisite
        came from local pooling) → local path.
        """
        if cascade_result is not None and getattr(cascade_result, "success", False):
            return cascade_result.provider_fee_usdt
        return await self._calculate_trader_fee(
            found_requisite.trader_id, payment_method, amount_usdt
        )

    async def _collect_traders_snapshot(self, payment_method_value: str) -> list:
        """Return the universe of trader IDs eligible-by-config for this payment method.

        Stored verbatim into ``order_creation_snapshots.traders_snapshot``.
        Filters (status=enabled, is_payin_active, active method_config for
        the requested payment method) are applied *in the query*, so every
        row in the returned list is already eligible — no need to dup the
        flags or the method config (rates/limits are recoverable from the
        live ``traders`` table when an admin opens the debug view).

        The previous format stored a full per-trader dict (~5–10× larger).
        Legacy rows are still readable by ``_build_trader_candidates``.
        """
        from app.modules.traders.models import Trader, TraderMethodConfig
        from app.common.enums.traders import TraderStatus

        method_enum = PaymentMethod(payment_method_value)
        stmt = (
            select(Trader.id, Trader.user_id)
            .join(TraderMethodConfig, TraderMethodConfig.trader_id == Trader.id)
            .where(
                Trader.status == TraderStatus.ENABLED,
                Trader.is_payin_active == True,  # noqa: E712
                TraderMethodConfig.is_active == True,  # noqa: E712
                TraderMethodConfig.payment_method == method_enum,
            )
        )
        rows = (await self.session.execute(stmt)).all()
        return [{"trader_id": tid, "user_id": uid} for tid, uid in rows]

    async def create_payin_order(
        self, merchant: Merchant, data: MerchantPayinCreate, source: OrderSource = OrderSource.API
    ) -> Order:
        snapshot: dict = {
            "request_data": {
                "amount": data.amount,
                "currency": data.currency.value,
                "payment_method": data.payment_method.value,
                "payment_option": data.payment_option,
                "issue_requisite_async": data.issue_requisite_async,
            },
            "merchant_snapshot": {
                "id": merchant.id,
                "user_id": merchant.user_id,
                "name": merchant.name,
                "status": merchant.status.value if merchant.status else None,
                "fees": merchant.fees,
                "currency": merchant.currency.value if merchant.currency else None,
                "order_ttl_seconds": merchant.order_ttl_seconds,
                "trader_groups": [g.name for g in (merchant.trader_groups or [])],
            },
            "rate_snapshot": None,
            "traders_snapshot": [],
            "candidates": [],
            "result": {"success": False, "selected_requisite_id": None, "error": None},
        }

        try:
            return await self._create_payin_order_inner(merchant, data, snapshot, source=source)
        except Exception as exc:
            snapshot["result"]["error"] = str(exc)
            raise
        finally:
            payin_snapshot_var.set(snapshot)

    async def _create_payin_order_inner(
        self, merchant: Merchant, data: MerchantPayinCreate, snapshot: dict, source: OrderSource = OrderSource.API
    ) -> Order:
        trace = _PayinTrace(f"m{merchant.id}")

        # Banned-client gate (fail fast, hot-path-cheap): a blocked clientID gets
        # no requisite. In-process ~5s cache over a Redis set → ~no per-order I/O
        # and fail-open (Redis down ⇒ not blocked). Opaque to the merchant: the
        # rejection is the SAME generic "no requisite" as a real miss.
        if await block_cache.is_blocked(merchant.id, data.userId):
            # Count the withheld requisite (telemetry, surfaced on the Clients
            # page). Only on this rejected branch — the success path is untouched.
            await block_cache.record_blocked_attempt(merchant.id, data.userId)
            raise NotFoundException("No available requisite found at the moment.")
        trace.stage("client_block")

        if data.internalId:
            existing = await self.repository.get_by_external_id_and_merchant(
                data.internalId, merchant.id
            )
            if existing:
                raise ValidationException(
                    f"Order with internalId '{data.internalId}' already exists"
                )
        trace.stage("idempotency_check")

        if data.payment_option:
            payment_option = await self.session.get(PaymentOption, data.payment_option)
            if not payment_option or not payment_option.is_active:
                raise ValidationException("Invalid payment option")

            if data.payment_method.value not in payment_option.supported_methods:
                raise ValidationException("Payment method is not supported by the selected option")

            if payment_option.currency != data.currency:
                raise ValidationException("Currency does not match the selected payment option")
        trace.stage("payment_option_check")

        order_uuid = uuid.uuid4()
        payment_url = f"{settings.CHECKOUT_BASE_URL}/pay/{order_uuid}"
        external_id = data.internalId or str(order_uuid)

        ttl = merchant.order_ttl_seconds
        date_end = utcnow() + timedelta(seconds=ttl)

        rate_service = RateService(self.session)
        config = None
        
        if merchant.rate_config_id:
            try:
                config = await rate_service.get_config(merchant.rate_config_id)
                if not config.is_active or not config.current_rate:
                    config = None
            except NotFoundException:
                config = None
        
        if not config:
            active_configs = await rate_service.get_active_configs()
            config = next((c for c in active_configs if c.fiat_currency == data.currency), None)
        trace.stage("rate_config")

        if config:
            snapshot["rate_snapshot"] = {
                "id": config.id,
                "name": config.name,
                "source": config.source.value if config.source else None,
                "fiat_currency": config.fiat_currency.value if config.fiat_currency else None,
                "current_rate": config.current_rate,
                "is_active": config.is_active,
                "last_updated_at": config.last_updated_at.isoformat() if config.last_updated_at else None,
            }
        
        if not config or not config.current_rate:
            raise ValidationException(f"Payments in {data.currency.value} are temporarily unavailable")
            
        exchange_rate = Decimal(str(config.current_rate))
        amount_usdt = (Decimal(str(data.amount)) / exchange_rate).quantize(Decimal("0.0000"))
        
        fee_percent = Decimal(str(merchant.fees.get(data.payment_method.value, 0)))
        fee_usdt = (amount_usdt * (fee_percent / Decimal("100"))).quantize(Decimal("0.0000"))
        profit_usdt = amount_usdt - fee_usdt

        order_data = {
            "uuid": order_uuid,
            "merchant_id": merchant.id,
            "external_id": external_id,
            "client_user_id": data.userId,
            "source": source,
            "direction": PaymentDirection.PAYIN,
            "payment_method": data.payment_method,
            "payment_option_id": data.payment_option or None,
            "amount": data.amount,
            "currency": data.currency,
            "amount_usdt": amount_usdt,
            "exchange_rate": exchange_rate,
            "fee_usdt": fee_usdt,
            "profit_usdt": profit_usdt,
            "webhook_url": data.notificationUrl,
            "payment_url": payment_url,
            "date_end": date_end,
        }

        snapshot["traders_snapshot"] = await self._collect_traders_snapshot(data.payment_method.value)
        trace.stage("traders_snapshot")

        if not data.issue_requisite_async:
            temp_order = Order(**order_data)
            pooling_service = PoolingService(self.session)
            pooling_result = await pooling_service.select_requisite_with_diagnostics(
                temp_order, await self._resolve_pooling_strategy()
            )
            trace.stage("pooling_select")

            snapshot["candidates"] = pooling_result.candidates

            found_requisite = pooling_result.selected
            cascade_result = None
            if not found_requisite and _cascade_enabled(merchant):
                from app.modules.cascading.service import CascadingService

                cascading = CascadingService(self.session)
                cascade_result = await cascading.try_cascade(
                    merchant=merchant,
                    order_data={**order_data, "payment_option_code": None},
                    snapshot=snapshot,
                )
                if cascade_result.success:
                    found_requisite = cascade_result.requisite
                trace.stage("cascade")

            if not found_requisite:
                raise NotFoundException("No available requisite found at the moment.")

            snapshot["result"]["success"] = True
            snapshot["result"]["selected_requisite_id"] = found_requisite.id
            if cascade_result and cascade_result.success:
                snapshot["result"]["cascade_provider_id"] = cascade_result.provider.id
                snapshot["result"]["cascade_provider_code"] = cascade_result.provider.code

            order_data["status"] = OrderStatus.PENDING
            order_data["requisite_id"] = found_requisite.id
            order_data["trader_id"] = found_requisite.trader_id
            order_data["trader_fee_usdt"] = await self._resolve_trader_fee(
                found_requisite=found_requisite,
                cascade_result=cascade_result,
                payment_method=data.payment_method.value,
                amount_usdt=amount_usdt,
            )
            trace.stage("trader_fee")
            # Inherit payment option from the selected requisite when the merchant didn't pin one
            if not order_data.get("payment_option_id") and found_requisite.payment_option_id:
                order_data["payment_option_id"] = found_requisite.payment_option_id

            async with self.session.begin_nested():

                is_cascade_routed = bool(cascade_result and cascade_result.success)
                if not is_cascade_routed:
                    await self._atomic_claim_requisite_capacity(
                        requisite_id=found_requisite.id,
                        amount=order_data["amount"],
                    )
                    trace.stage("limit_claim")
                elif cascade_result.attempt:
                    # Provider's order id — its presence marks a cascade-routed deal.
                    order_data["provider_order_id"] = cascade_result.attempt.external_order_id

                order = await self.repository.create(order_data)
                await self.session.execute(
                    update(Requisite)
                    .where(Requisite.id == found_requisite.id)
                    .values(last_used_at=utcnow())
                )

                from app.modules.finance.service import FinanceService
                finance_service = FinanceService(self.session)
                trader_user = await self.session.get(User, found_requisite.trader_id)
                await finance_service.create_order(order=order, trader=trader_user)

                if cascade_result and cascade_result.success and cascade_result.attempt:
                    cascade_result.attempt.order_id = order.id
                    self.session.add(cascade_result.attempt)
                    await self.session.flush()
            trace.stage("tx_commit")

            # Notify merchant that order is ready (PENDING with requisite)
            celery_app.send_task("app.workers.tasks.callbacks.send_order_callback", args=[order.id])
            trace.stage("celery_send")
            reloaded = await self._reload_order(order.id)
            trace.stage("reload")
            trace.flush()
            return reloaded
        else:
            order_data["status"] = OrderStatus.CREATED
            snapshot["result"]["success"] = True
            snapshot["result"]["async_mode"] = True
            async with self.session.begin_nested():
                order = await self.repository.create(order_data)

            celery_app.send_task("assign_requisite_task", args=[order.id])
            return await self._reload_order(order.id)

    async def assign_requisite(self, order_id: int) -> None:
        """
        Background task logic to find and assign a requisite to the order.
        """
        order = await self.repository.get(order_id)
        if not order or order.status != OrderStatus.CREATED:
            return

        # Re-check the ban here too: the client may have been blocked AFTER an
        # async order was created in CREATED. Blocked ⇒ never assign (leave it
        # in CREATED, same as a no-requisite outcome).
        if await block_cache.is_blocked(order.merchant_id, order.client_user_id):
            await block_cache.record_blocked_attempt(order.merchant_id, order.client_user_id)
            return

        pooling_service = PoolingService(self.session)

        found_requisite = await pooling_service.select_requisite(
            order, await self._resolve_pooling_strategy()
        )

        cascade_result = None
        if not found_requisite:
            merchant = await self.session.get(Merchant, order.merchant_id)
            if merchant and _cascade_enabled(merchant):
                from app.modules.cascading.service import CascadingService

                cascading = CascadingService(self.session)
                cascade_result = await cascading.try_cascade(
                    merchant=merchant,
                    order_data={
                        "id": order.id,
                        "amount": order.amount,
                        "amount_usdt": order.amount_usdt,
                        "exchange_rate": order.exchange_rate,
                        "fee_usdt": order.fee_usdt,
                        "currency": order.currency,
                        "payment_method": order.payment_method,
                        "payment_option_id": order.payment_option_id,
                        "payment_option_code": None,
                    },
                    snapshot={},
                )
                if cascade_result.success:
                    found_requisite = cascade_result.requisite

        if not found_requisite:
            # No requisite found, we leave it in CREATED state.
            # The background task might retry later, or it will timeout for the merchant.
            return

        async with self.session.begin_nested():
            trader_fee_usdt = await self._resolve_trader_fee(
                found_requisite=found_requisite,
                cascade_result=cascade_result,
                payment_method=order.payment_method.value,
                amount_usdt=order.amount_usdt,
            )

            extra_fields = {
                "requisite_id": found_requisite.id,
                "trader_id": found_requisite.trader_id,
                "trader_fee_usdt": trader_fee_usdt,
            }
            # Inherit payment option from the selected requisite when not yet set
            if not order.payment_option_id and found_requisite.payment_option_id:
                extra_fields["payment_option_id"] = found_requisite.payment_option_id

            # CREATED → PENDING through the single status funnel: writes the
            # assignment fields AND freezes the trader's collateral into ESCROW
            # (create_order). The requisite/cascade bookkeeping + notifications
            # below stay here; the webhook is fired once at the end.
            order = await self.change_status(
                order, OrderStatus.PENDING, extra_fields=extra_fields,
                audit_action=None, fire_callback=False,
            )

            # Update requisite last_used_at
            await self.session.execute(
                update(Requisite)
                .where(Requisite.id == found_requisite.id)
                .values(last_used_at=utcnow())
            )

            if cascade_result and cascade_result.success and cascade_result.attempt:
                cascade_result.attempt.order_id = order.id
                order.provider_order_id = cascade_result.attempt.external_order_id
                self.session.add(cascade_result.attempt)
                self.session.add(order)
                await self.session.flush()

        # Notify the API that the requisite has been assigned
        await redis_client.publish(f"order_updates:{order_id}", "assigned")
        # Notify merchant via webhook (order is now PENDING with requisite)
        celery_app.send_task("app.workers.tasks.callbacks.send_order_callback", args=[order_id])

    async def get_merchant_order(self, merchant: Merchant, order_id: str) -> Order:
        """
        Get an order by UUID for a specific merchant.
        """
        order = await self.repository.get_by_uuid_and_merchant(order_id, merchant.id)
        if not order:
            raise NotFoundException(f"Order {order_id} not found")
        return order

    async def cancel_order(
        self, 
        merchant: Merchant, 
        order_id: Optional[str] = None,
        external_id: Optional[str] = None,
        reason: str = "Cancelled by merchant"
    ) -> Order:
        """Cancel an order by order_id or external_id -> CANCELED (escrow released)."""
        if not order_id and not external_id:
            raise ValidationException("Either order_id or external_id must be provided")

        if order_id:
            order = await self.repository.get_by_uuid_and_merchant(order_id, merchant.id)
            error_msg = f"Order {order_id} not found"
        else:
            order = await self.repository.get_by_external_id_and_merchant(external_id, merchant.id)
            error_msg = f"Order with external ID {external_id} not found"
        if not order:
            raise NotFoundException(error_msg)
        if order.status not in CANCELLABLE_STATUSES:
            raise ConflictException("Order cannot be cancelled in its current state")

        order = await self.change_status(
            order, OrderStatus.CANCELED, actor_id=merchant.user_id, reason=reason,
            audit_action="cancel_order",
        )

        # Best-effort cancel notification to the upstream cascade provider.
        try:
            celery_app.send_task(
                "app.workers.tasks.cascade.cancel_provider_for_order",
                args=[order.id],
            )
        except Exception as exc:  # pragma: no cover -- defensive
            logger.warning(
                "cancel_provider_for_order enqueue failed (order_id=%s): %s",
                order.id, exc,
            )
        return order

    async def get_merchant_order_by_external_id(self, merchant: Merchant, external_id: str) -> Order:
        """
        Get an order by external ID for a specific merchant.
        """
        order = await self.repository.get_by_external_id_and_merchant(external_id, merchant.id)
        if not order:
            raise NotFoundException(f"Order with external ID {external_id} not found")
        return order

    async def confirm_order(
        self,
        merchant: Merchant,
        attachment: UploadFile,
        order_id: Optional[str] = None,
        external_id: Optional[str] = None,
        uploaded_by: ReceiptUploader = ReceiptUploader.MERCHANT,
        dispute_id: Optional[int] = None,
    ) -> Order:
        """Confirm a transfer by uploading a receipt (by order UUID or
        external_id). Thin transport wrapper: resolve the order + read the
        bytes, then delegate the whole receipt lifecycle to
        ``ReceiptService.upload`` (store, moderate, append, mirror, effects). A
        merchant may upload more than one receipt per order; ``dispute_id`` links
        the receipt to an appeal.

        The file format is gated HERE — this is the shared receipt-upload entry
        for confirm-transfer, the dispute-bot, and the merchant ``/receipt``
        re-attach, none of which validate upstream (the dispute-OPEN path
        validates separately via ``_collect_dispute_evidence``). ``upload`` itself
        only size-checks, so without this a renamed file would be stored as a
        trusted receipt / dispute evidence.
        """
        from app.modules.receipts.service import ReceiptService
        from app.modules.receipts.storage import ReceiptStorage

        if not order_id and not external_id:
            raise ValidationException("Either order_id or external_id must be provided")

        content = await attachment.read()
        ReceiptStorage.validate_format(content, attachment.filename)  # ext + magic-byte gate

        if order_id:
            order = await self.repository.get_by_uuid_and_merchant(order_id, merchant.id)
            error_msg = f"Order {order_id} not found"
        else:
            order = await self.repository.get_by_external_id_and_merchant(external_id, merchant.id)
            error_msg = f"Order with external ID {external_id} not found"
        if not order:
            raise NotFoundException(error_msg)

        return await ReceiptService(self.session).upload(
            order=order, merchant=merchant, content=content,
            filename=attachment.filename, uploaded_by=uploaded_by, dispute_id=dispute_id,
        )

    async def _resolve_pooling_strategy(self) -> PoolingStrategy:
        """Resolve the active requisite-pooling strategy from platform settings.

        Defaults to WEIGHTED (weighted-random by priority_score) and treats
        any unknown/garbage value the same way, so a misconfigured setting can
        never break order creation. An explicitly stored ``pooling_strategy``
        platform setting still wins over this fallback.
        """
        from app.modules.settings.service import SettingsService

        try:
            raw = await SettingsService(self.session).get_str("pooling_strategy")
            return PoolingStrategy(raw)
        except (ValueError, Exception):  # noqa: BLE001 — never break order creation
            return PoolingStrategy.WEIGHTED

    @staticmethod
    def _emit_selector_feedback(order: Order, signal: str) -> None:
        """Best-effort: tell the MAB selector how an order turned out.

        Enqueues an async Celery task so the bandit learns the trader's true
        conversion from real outcomes (``completed`` → reward 1, ``failed`` →
        reward 0). Idempotent by order id inside the selector. Emitted on every
        terminal transition regardless of the selection strategy, so the
        selector's model stays warm and ready to switch on. Broker errors are
        swallowed — feedback is telemetry, never part of the money path.
        """
        if not order.trader_id:
            return
        try:
            from app.workers.celery_app import celery_app
            celery_app.send_task(
                "record_selector_feedback",
                kwargs={
                    "selector_name": "traders",
                    "order_id": str(order.uuid),
                    "entity_id": str(order.trader_id),
                    "signal": signal,
                },
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.warning(
                "record_selector_feedback enqueue failed (order_id=%s): %s",
                order.id, exc,
            )

    async def change_status(
        self,
        order: Order,
        new_status: OrderStatus,
        *,
        actor_id: Optional[int] = None,
        reason: Optional[str] = None,
        audit_action: Optional[str] = "change_status",
        fire_callback: bool = True,
        extra_fields: Optional[dict] = None,
        force: bool = False,
    ) -> Order:
        """The single funnel for EVERY order status transition. Validates the
        move against the allowed-transition graph (``_ALLOWED_TRANSITIONS``),
        writes the status (+ matching timestamps and any caller ``extra_fields``),
        routes the matching escrow operation through FinanceService, projects the
        financial snapshot, audits, and fires the merchant webhook after commit.
        Status + finance + audit are ONE atomic nested tx.

        Each transition routes through exactly ONE FinanceService business
        function, which moves ALL of that operation's money (escrow, fees, trader
        + teamlead rewards, turnover) — change_status never moves money itself:
          * PENDING            → create_order  (freeze collateral on assignment)
          * SUCCESS            → complete_order (settle: escrow, fees, trader +
                                  teamlead rewards, turnover)
          * FAILED / CANCELED  → cancel_order   (release collateral)
          * DISPUTED           → reconcile_for_dispute (reverse the full
                                  settlement incl. teamlead rewards → frozen)
          * RECEIPT_UPLOADED   → none (status-only; the caller passes the receipt
                                  fields via ``extra_fields``)

        Callers keep their own NON-money effects (selector feedback, cascade
        notify, requisite assignment writes, receipt row, ...).

        ``force`` is the MANUAL-override escape hatch (admin panel / trader
        "mark as paid"): it skips the automatic-mode transition graph so an
        operator can move an order to any status — e.g. settle a FAILED order to
        SUCCESS without a dispute. The money stays correct: a force move between
        two *terminal* states first normalises the books back to the frozen
        (pending-like) state (``reconcile_for_dispute``) — reversing a prior
        SUCCESS's rewards/turnover — then applies the new exit settlement, the
        same way a dispute open+decide does.

        IDEMPOTENT UNDER CONCURRENCY: this funnel locks the order row
        (``SELECT … FOR UPDATE``) and re-reads its AUTHORITATIVE status before
        deciding. Callers reach it with an unlocked, possibly-stale order (a
        double-click, a retried request, dispute resolve racing trader-accept,
        admin override racing the bot, two concurrent dispute decisions). The
        lock makes the second caller block here, then either no-op on the
        same-status guard or hit a clean transition conflict — so the money
        funnel can NEVER run twice for one transition (no double settle).
        """
        # Lock the row + read the committed status (not the caller's snapshot).
        old_status = order.status
        if order.id is not None:
            locked_status = await self.repository.lock_status(order.id)
            if locked_status is not None:
                old_status = locked_status
        if new_status == old_status:
            return order
        if not force and new_status not in _ALLOWED_TRANSITIONS.get(old_status, frozenset()):
            raise ConflictException(
                f"Illegal order transition {old_status.value} → {new_status.value}"
            )

        fields: dict = dict(extra_fields or {})
        fields["status"] = new_status
        if new_status == OrderStatus.SUCCESS:
            fields["confirmed_at"] = utcnow()
        elif new_status in (OrderStatus.FAILED, OrderStatus.CANCELED, OrderStatus.REFUNDED):
            fields["rejected_at"] = utcnow()
            if reason is not None:
                fields["rejection_reason"] = reason

        from app.modules.finance.service import FinanceService

        async with self.session.begin_nested():
            order = await self.repository.update(order.id, fields)

            # Non-financial transitions (RECEIPT_UPLOADED) write status-only and
            # skip the principal lookups entirely — keeps the path lean.
            if new_status in _FINANCE_STATUSES:
                trader = await self.session.get(User, order.trader_id) if order.trader_id else None
                # Resolve the merchant only when a branch needs it. The PAYIN
                # assignment (CREATED→PENDING) is the payin hot path and only
                # touches the trader, so skip the counter-party lookup there.
                merchant = None
                if new_status != OrderStatus.PENDING or order.direction == PaymentDirection.PAYOUT:
                    merchant = await self.session.get(Merchant, order.merchant_id)
                finance_service = FinanceService(self.session)

                # Manual override between two terminal states: normalise the
                # books back to the frozen (pending-like) state first — exactly
                # like a dispute open — so the exit below settles/releases the
                # collateral correctly (a FAILED order's collateral sits in the
                # trader's WORK, not ESCROW). reconcile reverses the FULL prior
                # settlement, including teamlead rewards + turnover when SUCCESS.
                if force and old_status in _TERMINAL_STATUSES and new_status in _TERMINAL_STATUSES:
                    await finance_service.reconcile_for_dispute(
                        order=order, merchant=merchant, trader=trader, pre_status=old_status,
                    )

                teamlead_breakdown: list = []
                if new_status == OrderStatus.PENDING:
                    # Assignment: freeze the principal's collateral into ESCROW.
                    await finance_service.create_order(order=order, trader=trader, merchant=merchant)
                elif new_status == OrderStatus.SUCCESS:
                    # Settle EVERYTHING (escrow, fees, trader + teamlead rewards,
                    # turnover) — complete_order is the single source of truth and
                    # returns the teamlead breakdown for the snapshot.
                    teamlead_breakdown = await finance_service.complete_order(
                        order=order, trader=trader, merchant=merchant,
                    )
                elif new_status in (OrderStatus.FAILED, OrderStatus.CANCELED, OrderStatus.REFUNDED):
                    # Release frozen escrow to its owner; tolerate "nothing was
                    # frozen" exactly as the legacy fail/cancel paths did. REFUNDED
                    # is admin-only (see admin_update_order) and unwinds the order
                    # to the "never happened" state: a SUCCESS→REFUNDED force first
                    # reverses the settlement (reconcile above, terminal→terminal),
                    # then this release lands the collateral back in the trader.
                    try:
                        if (order.direction == PaymentDirection.PAYIN and trader) or \
                                order.direction == PaymentDirection.PAYOUT:
                            await finance_service.cancel_order(order=order, trader=trader, merchant=merchant)
                    except ValidationException as e:
                        if "Insufficient funds" not in str(e):
                            raise
                        logger.warning(
                            "Escrow release skipped (insufficient funds) on order %s -> %s: %s",
                            order.id, new_status.value, str(e),
                        )
                elif new_status == OrderStatus.DISPUTED:
                    # Freeze the books into the pending-like state (trader collateral
                    # back in ESCROW; any prior settlement reversed), regardless of
                    # the status the order was in when the dispute opened.
                    await finance_service.reconcile_for_dispute(
                        order=order, merchant=merchant, trader=trader, pre_status=old_status,
                    )

                # Project the denormalized financial snapshot onto the order
                # (ledger stays the source of truth). Finalize at SUCCESS from the
                # teamlead rewards complete_order just paid; zero it when the
                # settlement is released (FAILED / CANCELED).
                if new_status == OrderStatus.SUCCESS:
                    reward_total = sum(
                        (Decimal(str(b["reward_usdt"])) for b in teamlead_breakdown), Decimal("0"),
                    )
                    order = await self.repository.update(order.id, self._financial_snapshot(
                        order, teamlead_reward=reward_total,
                        teamlead_breakdown=teamlead_breakdown, settled=True,
                    ))
                elif new_status in (OrderStatus.FAILED, OrderStatus.CANCELED):
                    order = await self.repository.update(order.id, self._financial_snapshot(
                        order, teamlead_reward=Decimal("0"), teamlead_breakdown=[], settled=False,
                    ))

            if audit_action is not None:
                new_values: dict = {"status": new_status.value}
                if reason is not None:
                    new_values["reason"] = reason
                await self.audit_log(
                    action=audit_action, entity_type="order", entity_id=order.id,
                    user_id=actor_id, new_values=new_values,
                )

        if fire_callback:
            celery_app.send_task("app.workers.tasks.callbacks.send_order_callback", args=[order.id])
        return order

    def _financial_snapshot(
        self,
        order: Order,
        *,
        teamlead_reward: Decimal,
        teamlead_breakdown: list,
        settled: bool,
    ) -> dict:
        """Build the denormalized financial-snapshot update for an order — a
        read-model PROJECTION of the ledger (which stays the single source of
        truth). ``settled`` marks a realized SUCCESS settlement; when False
        (released / never settled) the teamlead reward and platform profit are
        zero, matching the ledger. ``financials`` carries the per-teamlead
        breakdown + the headline figures for one-shot reads / reporting.
        """
        fee = order.fee_usdt or Decimal("0")
        trader_fee = order.trader_fee_usdt or Decimal("0")
        reward = teamlead_reward if settled else Decimal("0")
        platform_profit = (fee - trader_fee - reward) if settled else Decimal("0")
        snapshot = {
            "amount_usdt": str(order.amount_usdt or Decimal("0")),
            "fee_usdt": str(fee),
            "trader_fee_usdt": str(trader_fee),
            "merchant_net_usdt": str(order.profit_usdt or Decimal("0")),
            "teamlead_reward_usdt": str(reward),
            "platform_profit_usdt": str(platform_profit),
            "teamlead_rewards": list(teamlead_breakdown) if settled else [],
            "settled": settled,
            "status": order.status.value,
        }
        return {
            "teamlead_reward_usdt": reward,
            "platform_profit_usdt": platform_profit,
            "financials": snapshot,
        }

    async def complete_order(
        self,
        trader: User,
        order_id: int,
    ) -> Order:
        """Trader confirms the money arrived -> order SUCCESS (escrow settles to
        merchant, system fee + teamlead rewards + turnover applied).

        Idempotent under concurrency: the order row is locked ``FOR UPDATE`` so
        two confirms for the same order (cabinet + trader-bot button, a
        double-click, or a retried request) can't both settle it — the second
        blocks until the first commits, then sees SUCCESS and returns the
        already-completed order instead of debiting the trader / crediting the
        merchant a second time."""
        order = await self.repository.get_for_update(order_id)
        if not order:
            raise NotFoundException("Order not found")
        if order.trader_id != trader.id:
            raise ForbiddenException("You are not assigned to this order")
        if order.status == OrderStatus.SUCCESS:
            # Already settled by a concurrent/earlier confirm — no-op, no double settle.
            return order
        if order.status not in COMPLETABLE_STATUSES:
            raise ConflictException("Order cannot be completed in its current state")

        order = await self.change_status(
            order, OrderStatus.SUCCESS, actor_id=trader.id, audit_action="complete_order",
        )
        # Reward signal for the MAB selector: this trader converted the order.
        self._emit_selector_feedback(order, "completed")
        return order

    async def complete_order_from_trader_group(
        self,
        order_uuid: str,
        telegram_group_id: int,
        *,
        actor_tg_id: Optional[int] = None,
    ) -> Order:
        """Trader confirms 'оплачено' straight from their Telegram group (the
        trader-bot inline button) — runs the SAME settlement as the cabinet
        ``complete_order``.

        Authorisation is group-based: the order's assigned trader must have THIS
        ``telegram_group_id`` (the trader-bot only ever posts a given order into
        that trader's own group), so a group can confirm only its own orders. The
        completable-state check + idempotency live in ``complete_order``.
        ``actor_tg_id`` is the Telegram user who tapped — for audit context only.
        """
        order = await self.get_order_for_trader_group(order_uuid, telegram_group_id)
        trader = await self.session.get(User, order.trader_id)
        if not trader:
            raise NotFoundException("Trader not found")
        return await self.complete_order(trader=trader, order_id=order.id)

    async def get_order_for_trader_group(
        self, order_uuid: str, telegram_group_id: int
    ) -> Order:
        """Resolve an order by uuid and authorise that THIS trader group owns it
        (the order's assigned trader has this ``telegram_group_id``). Shared by the
        trader-bot confirm + proof-request flows."""
        order = await self.repository.get_by_uuid(order_uuid)
        if not order:
            raise NotFoundException("Order not found")
        if not order.trader_id:
            raise ConflictException("Order has no assigned trader")

        from app.modules.traders.models import Trader

        group_id = (
            await self.session.execute(
                select(Trader.telegram_group_id).where(Trader.user_id == order.trader_id)
            )
        ).scalar_one_or_none()
        if not group_id or int(group_id) != int(telegram_group_id):
            raise ForbiddenException("This group is not authorised for this order")
        return order

    async def trader_settle_failed_order(
        self,
        trader: User,
        order_id: int,
    ) -> Order:
        """Trader manually settles an already-UNSUCCESSFUL order (FAILED /
        CANCELED) to SUCCESS — e.g. a late payment finally arrived. The trader's
        released collateral is re-frozen and settled to the merchant (+ the
        system fee / their reward) via the force override; no dispute needed.
        Only the assigned trader can do this, and only from a failed/canceled
        state (active orders use the ordinary ``complete_order``)."""
        order = await self.repository.get(order_id)
        if not order:
            raise NotFoundException("Order not found")
        if order.trader_id != trader.id:
            raise ForbiddenException("You are not assigned to this order")
        if order.status not in (OrderStatus.FAILED, OrderStatus.CANCELED):
            raise ConflictException(
                "Only an unsuccessful (failed / canceled) order can be settled this way"
            )

        order = await self.change_status(
            order, OrderStatus.SUCCESS, actor_id=trader.id,
            audit_action="trader_settle_failed", force=True,
        )
        self._emit_selector_feedback(order, "completed")
        return order

    async def fail_order(
        self,
        order_id: int,
        reason: str,
        trader: Optional[User] = None,
    ) -> Order:
        """Order -> FAILED (frozen escrow released back to its owner). Callable
        by the system (Celery timeout) or a specific trader."""
        order = await self.repository.get(order_id)
        if not order:
            raise NotFoundException(f"Order {order_id} not found")
        if trader and order.trader_id != trader.id:
            raise ForbiddenException("You are not assigned to this order")
        if order.status not in FAILABLE_STATUSES:
            raise ConflictException("Order cannot be failed in its current state")

        order = await self.change_status(
            order, OrderStatus.FAILED, actor_id=(trader.id if trader else None),
            reason=reason, audit_action="fail_order",
        )
        # Reward signal for the MAB selector: this trader did NOT convert.
        self._emit_selector_feedback(order, "failed")
        return order

    async def change_amount(
        self,
        order: Order,
        new_amount,
        *,
        actor_id: Optional[int] = None,
        reason: Optional[str] = None,
        audit_action: Optional[str] = "change_amount",
        fire_callback: bool = True,
    ) -> Order:
        """Change an order's fiat ``amount`` and reproject its USDT figures
        (``amount_usdt`` / ``fee_usdt`` / ``profit_usdt``) from the order's
        SNAPSHOT ``exchange_rate``, then post a CORRECTING (delta) ledger entry so
        the frozen ESCROW matches the new amount.

        Append-only: it NEVER reverses prior entries — the books move only by
        ``Δ = new − old`` (``FinanceService.recalculate_order``). The escrow
        adjustment runs only while the order is in an active/frozen state
        (PENDING / RECEIPT_UPLOADED / DISPUTED); for a settled order the row is
        re-projected without moving money. No-op (returns the order unchanged)
        when the amount is identical or there's no ``exchange_rate`` snapshot to
        reproject from. Status + finance + audit are ONE atomic nested tx; the
        merchant webhook fires after commit (``fire_callback``).
        """
        new_amount_dec = Decimal(str(new_amount))
        if new_amount_dec == Decimal(str(order.amount)):
            return order

        ESCROW_ACTIVE = {
            OrderStatus.PENDING,
            OrderStatus.RECEIPT_UPLOADED,
            OrderStatus.DISPUTED,
        }

        old_amount_usdt = order.amount_usdt or Decimal("0")
        fields: dict = {"amount": new_amount}
        old_values: dict = {"amount": float(order.amount)}
        new_values: dict = {"amount": float(new_amount_dec)}

        new_amount_usdt: Optional[Decimal] = None
        if order.exchange_rate:
            new_amount_usdt = (
                new_amount_dec / Decimal(str(order.exchange_rate))
            ).quantize(Decimal("0.0000"))
            fee_percent = Decimal("0")
            if order.merchant_id:
                merchant = await self.session.get(Merchant, order.merchant_id)
                if merchant:
                    fee_percent = Decimal(str(
                        merchant.fees.get(order.payment_method.value, 0)
                    ))
            new_fee_usdt = (new_amount_usdt * (fee_percent / Decimal("100"))).quantize(Decimal("0.0000"))
            fields["amount_usdt"] = new_amount_usdt
            fields["fee_usdt"] = new_fee_usdt
            fields["profit_usdt"] = new_amount_usdt - new_fee_usdt
            old_values["amount_usdt"] = float(order.amount_usdt) if order.amount_usdt else None
            new_values["amount_usdt"] = float(new_amount_usdt)
            # Re-scale the trader reward with the amount so a later re-settlement
            # (e.g. dispute DISPUTED→SUCCESS after an amount change) pays the
            # reward for the NEW amount, not the stale original (audit #19). The
            # dispute-open reversal undid the original (matching) value, so the
            # books stay balanced. Proportional keeps it method/cascade-agnostic.
            if order.trader_fee_usdt and old_amount_usdt and old_amount_usdt > 0:
                new_trader_fee = (
                    Decimal(str(order.trader_fee_usdt)) / old_amount_usdt * new_amount_usdt
                ).quantize(Decimal("0.0000"))
                fields["trader_fee_usdt"] = new_trader_fee
                old_values["trader_fee_usdt"] = float(order.trader_fee_usdt)
                new_values["trader_fee_usdt"] = float(new_trader_fee)

        from app.modules.finance.service import FinanceService

        async with self.session.begin_nested():
            order = await self.repository.update(order.id, fields)

            if new_amount_usdt is not None and order.status in ESCROW_ACTIVE:
                trader = await self.session.get(User, order.trader_id) if order.trader_id else None
                merchant = await self.session.get(Merchant, order.merchant_id)
                await FinanceService(self.session).recalculate_order(
                    order=order,
                    old_amount_usdt=old_amount_usdt,
                    new_amount_usdt=new_amount_usdt,
                    trader=trader,
                    merchant=merchant,
                )

            # Re-project the financial snapshot for the new amount: the
            # amount-proportional figures refresh; any prior teamlead reward /
            # settled state carries over (amount-on-SUCCESS reward recalc is a
            # later, dispute-driven concern).
            if new_amount_usdt is not None:
                prior = order.financials or {}
                order = await self.repository.update(order.id, self._financial_snapshot(
                    order,
                    teamlead_reward=order.teamlead_reward_usdt or Decimal("0"),
                    teamlead_breakdown=prior.get("teamlead_rewards", []),
                    settled=order.status == OrderStatus.SUCCESS,
                ))

            if audit_action is not None:
                if reason is not None:
                    new_values["reason"] = reason
                await self.audit_log(
                    action=audit_action, entity_type="order", entity_id=order.id,
                    user_id=actor_id, old_values=old_values, new_values=new_values,
                )

        if fire_callback:
            celery_app.send_task("app.workers.tasks.callbacks.send_order_callback", args=[order.id])
        return order

    async def admin_update_order(
        self,
        order_id: int,
        data: AdminOrderUpdate,
        admin_user_id: int,
    ) -> Order:
        """
        Admin override: update order status and/or amount.

        Status transitions trigger the same escrow / settlement flows as the
        regular trader/merchant endpoints — admin is just allowed to push them
        from any active state to any terminal state without ownership checks:

          • PENDING / RECEIPT_UPLOADED → SUCCESS
              ESCROW is moved to merchant WORK, system fee captured,
              teamlead rewards calculated, requisite turnover incremented.
          • PENDING / RECEIPT_UPLOADED / DISPUTED → CANCELED / FAILED
              ESCROW is released back to trader's WORK balance.

        Reverting a finalized order (SUCCESS/CANCELED/FAILED → anything) is
        rejected — escrow has already been settled and re-running the flow
        would double-count balances.

        When `amount` changes on an active order, ESCROW is rebalanced via
        ``recalculate_order``. New `amount_usdt`, `fee_usdt`, `profit_usdt`
        are recomputed from the order's current exchange rate.
        """
        order = await self.repository.get(order_id)
        if not order:
            raise NotFoundException(f"Order {order_id} not found")

        TERMINAL = {
            OrderStatus.SUCCESS,
            OrderStatus.FAILED,
            OrderStatus.CANCELED,
            OrderStatus.REFUNDED,
        }

        old_status = order.status
        new_status = (
            data.status if (data.status is not None and data.status != old_status) else None
        )
        status_changed = new_status is not None

        if status_changed:
            # Manual admin override may set ANY terminal status — e.g. settle a
            # FAILED order to SUCCESS without a dispute. The money is normalised
            # correctly by change_status(force=True). Re-activating to a non-
            # terminal status has no sensible money semantics, so it's blocked.
            if new_status not in TERMINAL:
                raise ConflictException(
                    f"Admin can only set a terminal status (success / failed / "
                    f"canceled / refunded), not {new_status.value}"
                )
            from app.common.enums.disputes import DisputeStatus

            open_dispute = (await self.session.execute(
                select(Dispute.id).where(
                    Dispute.order_id == order.id,
                    Dispute.status == DisputeStatus.OPEN,
                ).limit(1)
            )).scalar_one_or_none()
            if open_dispute is not None:
                raise ConflictException(
                    "Order has an open dispute — resolve or reject it via the "
                    "dispute flow; admin status override is blocked to avoid "
                    "double-releasing escrow."
                )

        amount_changed = data.amount is not None and data.amount != float(order.amount)

        # An amount change is a dispute-resolution tool: it's allowed ONLY while
        # the order has an ACTIVE dispute (status DISPUTED), where change_amount
        # rebalances the frozen ESCROW by the delta. On any other status the
        # collateral is either not adjustable this way or already settled/released,
        # so rewriting the amount would desync the row from the ledger. (Status
        # force-overrides below stay allowed WITHOUT a dispute — e.g. settle a
        # FAILED order to SUCCESS. Amount and status can't combine: a DISPUTED
        # order's status change is blocked by the open-dispute guard above.)
        if amount_changed and old_status != OrderStatus.DISPUTED:
            raise ConflictException(
                "The order amount can only be changed while it has an active dispute"
            )

        if not status_changed and not amount_changed:
            return order

        # Snapshot the old/new for the single combined admin audit row.
        old_values: dict = {}
        new_values: dict = {}
        if status_changed:
            old_values["status"] = old_status.value
            new_values["status"] = new_status.value
        if amount_changed:
            old_values["amount"] = float(order.amount)
            old_values["amount_usdt"] = float(order.amount_usdt) if order.amount_usdt else None
            new_values["amount"] = data.amount

        # 1. Amount delta FIRST: rebalance the frozen ESCROW to the new amount via
        #    the public ``change_amount`` funnel (a correcting/delta ledger entry)
        #    BEFORE any settlement runs — so a combined amount + status-to-terminal
        #    call settles/releases the *new* amount and never leaks Δ = new − old.
        if amount_changed:
            # Fire the merchant webhook here only when the status isn't ALSO
            # changing — otherwise change_status fires the single callback for
            # the whole edit (one webhook per admin action).
            order = await self.change_amount(
                order, data.amount, actor_id=admin_user_id, audit_action=None,
                fire_callback=not status_changed,
            )
            new_values["amount_usdt"] = float(order.amount_usdt) if order.amount_usdt else None
            new_values["fee_usdt"] = float(order.fee_usdt) if order.fee_usdt else None

        # 2. Status finalisation through the single status funnel (escrow settle /
        #    release + teamlead/turnover on SUCCESS, merchant webhook). The admin
        #    status-history row is recorded alongside it.
        if status_changed:
            self.session.add(OrderStatusHistory(
                order_id=order.id,
                old_status=old_status,
                new_status=new_status,
                changed_by_user_id=admin_user_id,
                reason=data.reason or "Admin override",
            ))
            order = await self.change_status(
                order, new_status, actor_id=admin_user_id,
                reason=data.reason or "Admin override", audit_action=None,
                force=True,
            )

        # One combined audit row for the admin override (status and/or amount).
        await self.audit_log(
            action="admin_update_order",
            entity_type="order",
            entity_id=order.id,
            user_id=admin_user_id,
            old_values=old_values,
            new_values=new_values,
        )

        return order

    async def list_merchant_orders(
        self,
        merchant_id: int,
        *,
        status: Optional["OrderStatus"] = None,
        payment_method: Optional["PaymentMethod"] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[Order]:
        return await self.repository.list_orders(
            merchant_ids=merchant_id,
            statuses=[status] if status else None,
            payment_method=payment_method,
            search=search, search_scope="merchant",
            skip=skip, limit=limit,
        )

    async def count_merchant_orders(
        self,
        merchant_id: int,
        *,
        status: Optional["OrderStatus"] = None,
        payment_method: Optional["PaymentMethod"] = None,
        search: Optional[str] = None,
    ) -> int:
        """Total orders for one terminal matching the filters — pagination header."""
        return await self.repository.count_orders(
            merchant_ids=merchant_id,
            statuses=[status] if status else None,
            payment_method=payment_method,
            search=search, search_scope="merchant",
        )

    async def _user_merchant_ids(self, user_id: int) -> list[int]:
        return list(
            (await self.session.execute(
                select(Merchant.id).where(Merchant.user_id == user_id)
            )).scalars().all()
        )

    async def list_orders_for_user_merchants(
        self,
        user_id: int,
        *,
        status: Optional["OrderStatus"] = None,
        payment_method: Optional["PaymentMethod"] = None,
        search: Optional[str] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[Order]:
        """List orders across every terminal that belongs to the given merchant owner."""
        merchant_ids = await self._user_merchant_ids(user_id)
        if not merchant_ids:
            return []
        return await self.repository.list_orders(
            merchant_ids=merchant_ids,
            statuses=[status] if status else None,
            payment_method=payment_method,
            search=search, search_scope="merchant",
            skip=skip, limit=limit,
        )

    async def count_orders_for_user_merchants(
        self,
        user_id: int,
        *,
        status: Optional["OrderStatus"] = None,
        payment_method: Optional["PaymentMethod"] = None,
        search: Optional[str] = None,
    ) -> int:
        """Total orders across all of the owner's terminals — pagination header."""
        merchant_ids = await self._user_merchant_ids(user_id)
        if not merchant_ids:
            return 0
        return await self.repository.count_orders(
            merchant_ids=merchant_ids,
            statuses=[status] if status else None,
            payment_method=payment_method,
            search=search, search_scope="merchant",
        )

    async def list_orders_for_export(
        self,
        user_id: int,
        *,
        date_from: datetime,
        date_to: datetime,
        limit: int = 100_000,
    ) -> list[Order]:
        """Orders across ALL of the owner's terminals within ``[from, to]`` —
        the data set for the merchant-cabinet Excel export. Spans every
        terminal (the export carries a terminal_id column to distinguish them).
        """
        merchant_ids = (
            (await self.session.execute(
                select(Merchant.id).where(Merchant.user_id == user_id)
            ))
            .scalars()
            .all()
        )
        if not merchant_ids:
            return []
        return await self.repository.list_orders(
            merchant_ids=merchant_ids,
            created_from=date_from, created_to=date_to,
            order="asc", limit=limit,
        )

    async def get_order_by_uuid(self, order_id: str) -> Optional[Order]:
        return await self.repository.get_by_uuid(order_id)

    async def get_trader_order_by_uuid(self, order_id: str, trader_user_id: int) -> Optional[Order]:
        """Returns an order only if it is assigned to the given trader (by user_id).

        `orders.trader_id` is a FK to `users.id` (not `traders.id`) — see
        `orders/models.py`. So we just compare against the authenticated
        user's id directly, without resolving the Trader row. The previous
        implementation compared `order.trader_id` (a user_id) against
        `Trader.id` (the traders-table PK), which never matched and caused
        every trader-side receipt download to return 404.
        """
        import uuid as _uuid
        try:
            _uuid.UUID(order_id)
        except ValueError:
            return None
        order = await self.repository.get_by_uuid(order_id)
        if order and order.trader_id == trader_user_id:
            return order
        return None

    async def list_trader_orders(
        self,
        trader_id: int,
        *,
        status: Optional[OrderStatus] = None,
        payment_method: Optional[PaymentMethod] = None,
        id_search: Optional[str] = None,
        amount_from: Optional[float] = None,
        amount_to: Optional[float] = None,
        skip: int = 0,
        limit: int = 50,
    ) -> list[Order]:
        return await self.repository.list_orders(
            trader_id=trader_id,
            statuses=[status] if status else None,
            payment_method=payment_method,
            search=id_search, search_scope="trader",
            amount_from=amount_from,
            amount_to=amount_to,
            skip=skip, limit=limit,
        )

    async def count_trader_orders(
        self,
        trader_id: int,
        *,
        status: Optional[OrderStatus] = None,
        payment_method: Optional[PaymentMethod] = None,
        id_search: Optional[str] = None,
        amount_from: Optional[float] = None,
        amount_to: Optional[float] = None,
    ) -> int:
        """Total trader orders matching the filters — for the pagination header."""
        return await self.repository.count_orders(
            trader_id=trader_id,
            statuses=[status] if status else None,
            payment_method=payment_method,
            search=id_search, search_scope="trader",
            amount_from=amount_from,
            amount_to=amount_to,
        )

    async def list_active_bot_orders(self, merchant_id: int) -> list[Order]:
        return await self.repository.list_orders(
            merchant_ids=merchant_id,
            source=OrderSource.BOT,
            statuses=ACTIVE_STATUSES,
            limit=200,
        )

    async def list_active_trader_orders(self, trader_id: int) -> list[Order]:
        return await self.repository.list_orders(
            trader_id=trader_id,
            statuses=[
                OrderStatus.PENDING,
                OrderStatus.RECEIPT_UPLOADED,
                OrderStatus.DISPUTED,
            ],
        )

    async def list_disputed_trader_orders(
        self, trader_id: int, *, skip: int = 0, limit: int = 50
    ) -> list[Order]:
        return await self.repository.list_orders(
            trader_id=trader_id,
            statuses=[OrderStatus.DISPUTED],
            skip=skip, limit=limit,
        )

    async def list_admin_orders(
        self,
        *,
        merchant_id: Optional[int] = None,
        trader_id: Optional[int] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        id_search: Optional[str] = None,
        trader_login: Optional[str] = None,
        merchant_login: Optional[str] = None,
        payment_method: Optional[str] = None,
        amount_from: Optional[float] = None,
        amount_to: Optional[float] = None,
        skip: int = 0,
        limit: int = 100,
    ) -> list[Order]:
        return await self.repository.list_orders(
            merchant_ids=merchant_id,
            trader_id=trader_id,
            statuses=[status] if status else None,
            payment_method=payment_method,
            amount_from=amount_from,
            amount_to=amount_to,
            search=id_search, search_scope="admin",
            login_search=search,
            trader_login=trader_login,
            merchant_login=merchant_login,
            skip=skip, limit=limit,
        )

    async def count_admin_orders(
        self,
        *,
        merchant_id: Optional[int] = None,
        trader_id: Optional[int] = None,
        status: Optional[str] = None,
        search: Optional[str] = None,
        id_search: Optional[str] = None,
        trader_login: Optional[str] = None,
        merchant_login: Optional[str] = None,
        payment_method: Optional[str] = None,
        amount_from: Optional[float] = None,
        amount_to: Optional[float] = None,
    ) -> int:
        """Total admin orders matching the filters — for the pagination header."""
        return await self.repository.count_orders(
            merchant_ids=merchant_id,
            trader_id=trader_id,
            statuses=[status] if status else None,
            payment_method=payment_method,
            amount_from=amount_from,
            amount_to=amount_to,
            search=id_search, search_scope="admin",
            login_search=search,
            trader_login=trader_login,
            merchant_login=merchant_login,
        )

    async def get_orders_for_merchant_in_period(
        self, merchant_id: int, start_time: datetime, end_time: Optional[datetime] = None
    ) -> list[Order]:
        """
        Get all orders for a specific merchant within a time period.
        """
        return await self.repository.list_orders(
            merchant_ids=merchant_id,
            created_from=start_time, created_to=end_time,
            order=None,
        )

    async def get_order_debug_info(self, identifier: str) -> OrderDebugResponse:
        """
        Get a comprehensive debug view of an order including all related logs,
        transactions, and history. The identifier can be ID, UUID, or external_id.
        """
        # 1. Find the order
        order = None
        if identifier.isdigit():
            order = await self.repository.get(int(identifier))
            
        if not order:
            try:
                uuid_obj = uuid.UUID(identifier)
                order = await self.repository.get_by_uuid(str(uuid_obj))
            except ValueError:
                pass
                
        if not order:
            # Fallback to external_id
            stmt = select(Order).where(Order.external_id == identifier)
            result = await self.session.execute(stmt)
            order = result.scalar_one_or_none()
            
        if not order:
            raise NotFoundException(f"Order {identifier} not found")

        # 2. Get status history
        stmt = select(OrderStatusHistory).where(OrderStatusHistory.order_id == order.id).order_by(OrderStatusHistory.created_at.asc())
        result = await self.session.execute(stmt)
        status_history = result.scalars().all()

        # 3. Get merchant API logs
        from app.modules.audit import repository as _ch_logs
        api_logs = await _ch_logs.list_logs_for_order(order.id)

        # 4. Get callback attempts
        stmt = select(CallbackAttempt).where(CallbackAttempt.order_id == order.id).order_by(CallbackAttempt.created_at.asc())
        result = await self.session.execute(stmt)
        callback_attempts = result.scalars().all()

        # 5. Get ledger entries
        stmt = select(LedgerEntry).where(LedgerEntry.reference_id == str(order.id)).order_by(LedgerEntry.created_at.asc())
        result = await self.session.execute(stmt)
        ledger_entries = result.scalars().all()

        # 6. Get dispute (if any)
        stmt = select(Dispute).where(Dispute.order_id == order.id)
        result = await self.session.execute(stmt)
        dispute_response = result.scalar_one_or_none()

        traders_candidates = await self._build_trader_candidates(order)

        return OrderDebugResponse(
            order=order,
            status_history=status_history,
            merchant_api_logs=api_logs,
            callback_attempts=callback_attempts,
            ledger_entries=ledger_entries,
            dispute=dispute_response,
            traders_candidates=traders_candidates,
        )

    async def _build_trader_candidates(self, order: Order) -> list:
        """Build a list of trader candidates with is_selected flag.

        Merges candidates + traders_snapshot from the order's creation snapshot
        (ClickHouse, if any) and marks the currently selected trader based on
        `order.requisite_id`. Only includes traders with status='enabled',
        is_payin_active=True, and an active method_config for the method.
        """
        from app.modules.audit import repository as _ch_logs
        from app.modules.requisites.models import Requisite
        from app.modules.traders.models import Trader

        snapshot = await _ch_logs.get_latest_snapshot_for_order(order.id)

        candidates_raw = list((snapshot.get("candidates") or []) if snapshot else [])

        traders_raw = []
        if snapshot and snapshot.get("traders_snapshot"):
            for t in snapshot["traders_snapshot"]:
                if not isinstance(t, dict):
                    continue
                # New minimal format only has trader_id / user_id — writer
                # guarantees the row was already eligible, so we accept
                # those unconditionally. Legacy rows carry status flags
                # and a method_config; re-apply the old filter for them
                # so a once-eligible-but-since-disabled trader doesn't
                # show as a candidate in the debug view.
                status = t.get("status")
                if status is not None and status != "enabled":
                    continue
                if "is_payin_active" in t and not t.get("is_payin_active"):
                    continue
                m_cfg = t.get("method_config")
                if m_cfg is not None and not m_cfg.get("is_active"):
                    continue
                traders_raw.append(t)

        selected_trader_user_id: Optional[int] = None
        if order.requisite_id:
            res = await self.session.execute(
                select(Trader.user_id).join(Requisite, Requisite.trader_id == Trader.id).where(Requisite.id == order.requisite_id)
            )
            selected_trader_user_id = res.scalar_one_or_none()

        trader_user_ids: set = set()
        for item in candidates_raw + traders_raw:
            if isinstance(item, dict):
                uid = item.get("user_id")
                if uid is not None:
                    trader_user_ids.add(int(uid))
        if selected_trader_user_id is not None:
            trader_user_ids.add(int(selected_trader_user_id))

        usernames: dict = {}
        if trader_user_ids:
            res = await self.session.execute(
                select(User.id, User.username).where(User.id.in_(trader_user_ids))
            )
            usernames = dict(res.all())

        def _to_item(src: dict, *, is_excluded: bool = False) -> dict:
            user_id = src.get("user_id")
            return {
                "trader_id": src.get("trader_id"),
                "user_id": user_id,
                "username": usernames.get(user_id) if user_id else None,
                "requisite_id": src.get("requisite_id"),
                "is_selected": bool(
                    selected_trader_user_id is not None and user_id == selected_trader_user_id
                ),
                "is_excluded": is_excluded,
                "reason": src.get("reason"),
                "status": src.get("status"),
                "is_payin_active": src.get("is_payin_active"),
                "is_payout_active": src.get("is_payout_active"),
                "method_config": src.get("method_config"),
            }

        items: list = []
        seen_keys: set = set()

        for c in candidates_raw:
            if not isinstance(c, dict):
                continue
            key = (c.get("user_id"), c.get("requisite_id"))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            items.append(_to_item(c))

        for t in traders_raw:
            key = (t.get("user_id"), None)
            existing = next((i for i in items if i.get("user_id") == t.get("user_id")), None)
            if existing:
                if not existing.get("status") and t.get("status"):
                    existing["status"] = t.get("status")
                if existing.get("is_payin_active") is None:
                    existing["is_payin_active"] = t.get("is_payin_active")
                if existing.get("is_payout_active") is None:
                    existing["is_payout_active"] = t.get("is_payout_active")
                if not existing.get("method_config") and t.get("method_config"):
                    existing["method_config"] = t.get("method_config")
                continue
            if key in seen_keys:
                continue
            seen_keys.add(key)
            items.append(_to_item(t, is_excluded=False))

        if selected_trader_user_id is not None and not any(i.get("is_selected") for i in items):
            items.insert(0, {
                "trader_id": None,
                "user_id": selected_trader_user_id,
                "username": usernames.get(selected_trader_user_id),
                "requisite_id": order.requisite_id,
                "is_selected": True,
                "is_excluded": False,
                "reason": None,
                "status": None,
                "is_payin_active": None,
                "is_payout_active": None,
                "method_config": None,
            })

        items.sort(key=lambda x: (not x.get("is_selected"), x.get("is_excluded"), x.get("username") or ""))
        return items


_MSK = timezone(timedelta(hours=3))


class ExportService(BaseService):
    """Builds merchant-facing Excel (.xlsx) exports of orders.

    Pure presentation over order data — no money mutation. It owns the whole
    export operation: it asks ``OrderService`` for the rows (data access stays
    there) and serialises them here. ``EXPORT_HEADERS`` and
    ``order_to_export_row`` are positional partners — keep them in lockstep.
    """

    # Column headers, in export order. Indexed positionally by
    # ``order_to_export_row`` — do not reorder without updating it (+ tests).
    EXPORT_HEADERS: List[str] = [
        "Внешний ID",
        "UUID",
        "Сумма RUB",
        "Сумма USDT",
        "Статус",
        "Курс обмена",
        "Комиссия сервиса USDT",
        "Дата создания МСК",
    ]

    @staticmethod
    def _to_msk_naive(dt: Optional[datetime]) -> Optional[datetime]:
        """Aware (or naive-UTC) datetime → MSK, tz stripped for openpyxl."""
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_MSK).replace(tzinfo=None)

    @staticmethod
    def _num(value: Any) -> Optional[float]:
        """Decimal/None → float/None so Excel treats the cell as a number."""
        if value is None:
            return None
        if isinstance(value, Decimal):
            return float(value)
        return value

    def order_to_export_row(self, order: Any) -> List[Any]:
        """Map one ``Order`` to its export-row values (positional, matches
        ``EXPORT_HEADERS``)."""
        status = order.status.value if hasattr(order.status, "value") else order.status
        return [
            order.external_id,              # внешний ID мерча
            str(order.uuid),
            self._num(order.amount),        # RUB
            self._num(order.amount_usdt),
            status,
            self._num(order.exchange_rate),
            self._num(order.fee_usdt),      # комиссия сервиса
            self._to_msk_naive(order.created_at),
        ]

    def build_orders_xlsx(self, orders: Sequence[Any]) -> bytes:
        """Serialise ``orders`` into an .xlsx workbook and return its bytes."""
        # Lazy import: openpyxl is only needed for exports, not on the order
        # hot path that imports this module on every request.
        from io import BytesIO

        from openpyxl import Workbook
        from openpyxl.styles import Font

        wb = Workbook()
        ws = wb.active
        ws.title = "Orders"

        ws.append(self.EXPORT_HEADERS)
        bold = Font(bold=True)
        for cell in ws[1]:
            cell.font = bold

        for order in orders:
            ws.append(self.order_to_export_row(order))

        # Reasonable fixed widths so the report is readable on open.
        widths = [22, 38, 14, 14, 16, 12, 22, 22]
        for idx, width in enumerate(widths, start=1):
            ws.column_dimensions[ws.cell(row=1, column=idx).column_letter].width = width

        buf = BytesIO()
        wb.save(buf)
        return buf.getvalue()

    async def export_orders_xlsx(
        self,
        user_id: int,
        *,
        date_from: datetime,
        date_to: datetime,
        limit: int = 100_000,
    ) -> bytes:
        """Full export operation: fetch the owner's orders for the period
        (across all terminals) and serialise to .xlsx bytes. ``limit`` is an
        OOM backstop — a truncated result is logged loudly."""
        orders = await OrderService(self.session).list_orders_for_export(
            user_id, date_from=date_from, date_to=date_to, limit=limit,
        )
        if len(orders) >= limit:
            logger.warning(
                "merchant_orders_export_truncated user_id=%s cap=%s from=%s to=%s",
                user_id, limit, date_from, date_to,
            )
        return self.build_orders_xlsx(orders)