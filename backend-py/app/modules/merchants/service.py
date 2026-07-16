import secrets
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundException, ValidationException
from app.core.security import encrypt_api_secret
from app.modules.base.service import BaseService
from app.modules.merchants.auth_cache import merchant_auth_cache
from app.modules.merchants.models import Merchant
from app.modules.merchants.repository import MerchantRepository


def normalize_telegram_users(raw: Any) -> List[Dict[str, Any]]:
    """Normalize telegram_user_ids into a canonical list of {id, label, active} dicts.

    Accepts mixed payloads:
        - list of int (legacy)
        - list of {id, label} objects (legacy after v1)
        - list of {id, label, active} objects (current)
        - list of Pydantic models with .id/.label/.active attributes
    Duplicate ids are deduplicated (last wins).
    The `active` flag marks which terminal is currently selected by this
    TG user; it is maintained by the bot state endpoints, not by user input.
    """
    if not raw:
        return []
    result: Dict[int, Dict[str, Any]] = {}
    for item in raw:
        if isinstance(item, int):
            if item in result and result[item].get("label"):
                continue
            result[item] = {"id": item, "label": None, "active": False}
            continue
        if isinstance(item, dict):
            try:
                tid = int(item.get("id"))
            except (TypeError, ValueError):
                continue
            label = item.get("label")
            active = bool(item.get("active", False))
            result[tid] = {
                "id": tid,
                "label": label if label else None,
                "active": active,
            }
            continue
        tid = getattr(item, "id", None)
        if tid is None:
            continue
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            continue
        label = getattr(item, "label", None)
        active = bool(getattr(item, "active", False))
        result[tid] = {
            "id": tid,
            "label": label if label else None,
            "active": active,
        }
    return list(result.values())


def extract_telegram_ids(raw: Any) -> List[int]:
    """Return just the numeric ids from a mixed legacy/new telegram list."""
    return [entry["id"] for entry in normalize_telegram_users(raw)]

class MerchantService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = MerchantRepository(session)

    # ── Admin ──────────────────────────────────────────────

    async def list_all(
        self,
        skip: int = 0,
        limit: int = 100,
        search: Optional[str] = None,
        *,
        status: Optional[str] = None,
        is_active: Optional[bool] = None,
        payment_method: Optional[str] = None,
    ) -> List[Merchant]:
        """List all merchants (admin)."""
        return await self.repository.get_all(
            skip=skip,
            limit=limit,
            search=search,
            status=status,
            is_active=is_active,
            payment_method=payment_method,
        )

    async def get_by_api_key(self, api_key: str) -> Optional[Merchant]:
        """Fetch merchant by their public API key."""
        return await self.repository.get_by_api_key(api_key)

    async def get_by_id(self, merchant_id: int) -> Merchant:
        """Fetch merchant by their internal ID."""
        merchant = await self.repository.get(merchant_id)
        if not merchant:
            raise NotFoundException(f"Merchant {merchant_id} not found")
        return merchant

    # ── User's merchants ───────────────────────────────────

    async def list_user_merchants(self, user_id: int) -> List[Merchant]:
        return await self.repository.list_by_user_id(user_id)

    async def list_by_telegram_user_id(self, tg_user_id: int) -> List[Merchant]:
        return await self.repository.list_by_telegram_user_id(tg_user_id)

    async def get_active_terminal_for_tg(self, tg_user_id: int) -> Optional[Merchant]:
        return await self.repository.get_active_terminal_for_tg(tg_user_id)

    async def set_active_terminal_for_tg(
        self, tg_user_id: int, merchant_id: int
    ) -> Merchant:
        """Mark `merchant_id` as the active terminal for the TG user, unset it on others."""
        target = await self.repository.get(merchant_id)
        if not target:
            raise NotFoundException(f"Merchant {merchant_id} not found")
        target_ids = [int(e["id"]) for e in normalize_telegram_users(target.telegram_user_ids or [])]
        if tg_user_id not in target_ids:
            raise ValidationException(
                "Telegram user is not authorized for this merchant"
            )

        all_merchants = await self.repository.list_by_telegram_user_id(tg_user_id)
        async with self.session.begin_nested():
            for m in all_merchants:
                entries = normalize_telegram_users(m.telegram_user_ids or [])
                changed = False
                for entry in entries:
                    if int(entry["id"]) != tg_user_id:
                        continue
                    should_be_active = m.id == merchant_id
                    if bool(entry.get("active")) != should_be_active:
                        entry["active"] = should_be_active
                        changed = True
                if changed:
                    await self.repository.update(m.id, {"telegram_user_ids": entries})

            await self.audit_log(
                action="tg_set_active_terminal",
                entity_type="merchant",
                entity_id=merchant_id,
                user_id=None,
                new_values={"telegram_user_id": tg_user_id},
            )

        for m in all_merchants:
            await merchant_auth_cache.invalidate(merchant_id=m.id)
        return await self.repository.get(merchant_id)

    async def get_merchant_for_user(
        self, user_id: int, merchant_id: Optional[int] = None,
    ) -> Merchant:
        """
        Resolve which merchant profile to use.
        If merchant_id is given, verify it belongs to user.
        Otherwise return the first (or auto-provision one).
        """
        if merchant_id:
            merchant = await self.repository.get_by_id_and_user(merchant_id, user_id)
            if not merchant:
                raise NotFoundException("Merchant not found or does not belong to this user")
            return merchant

        merchant = await self.repository.get_by_user_id(user_id)
        if not merchant:
            merchant = await self._provision_merchant(user_id)
        return merchant

    async def create_merchant(
        self, user_id: int, *, name: Optional[str] = None,
    ) -> Tuple[Merchant, str, str]:
        """Create a new merchant profile. Returns (merchant, api_key, api_secret_plain)."""
        from app.common.enums.users import UserRole
        from app.modules.users.models import User

        user = await self.session.get(User, user_id)
        if not user or user.role != UserRole.MERCHANT:
            raise ValidationException("Only users with merchant role can create merchants")

        api_key = secrets.token_hex(16)
        api_secret = secrets.token_urlsafe(32)

        async with self.session.begin_nested():
            merchant = await self.repository.create({
                "user_id": user_id,
                "name": name,
                "api_key": api_key,
                "api_secret": encrypt_api_secret(api_secret),
            })

            from app.modules.finance.service import FinanceService
            from app.common.enums.balances import BalanceType
            from app.common.enums.finances import Currency
            finance_service = FinanceService(self.session)
            await finance_service.get_or_create_merchant_balance(merchant, Currency.USDT, BalanceType.WORK)
            await finance_service.get_or_create_merchant_balance(merchant, Currency.USDT, BalanceType.ESCROW)

            await self.audit_log(
                action="create_merchant",
                entity_type="merchant",
                entity_id=merchant.id,
                user_id=user_id,
                new_values={"name": name},
            )

        return merchant, api_key, api_secret

    async def _provision_merchant(self, user_id: int) -> Merchant:
        """Auto-create a default merchant record for a user with role=merchant."""
        merchant, _, _ = await self.create_merchant(user_id, name="Default")
        return merchant

    # ── Settings / keys ────────────────────────────────────

    async def update_settings(
        self, merchant_id: int, updates: Dict[str, Any], user_id: Optional[int] = None
    ) -> Merchant:
        """Update merchant settings (webhook_url, ttl, timeout, trader/group bindings)."""
        merchant = await self.repository.get(merchant_id)
        if not merchant:
            raise NotFoundException(f"Merchant {merchant_id} not found")

        if not updates:
            return merchant

        trader_ids = updates.pop("trader_ids", None)
        group_ids = updates.pop("group_ids", None)
        cascade_group_ids = updates.pop("cascade_group_ids", None)
        if "cascade_mode" in updates and isinstance(updates["cascade_mode"], str):
            from app.common.enums.cascading import CascadeMode

            updates["cascade_mode"] = CascadeMode(updates["cascade_mode"])

        if "telegram_user_ids" in updates:
            incoming = normalize_telegram_users(updates["telegram_user_ids"])
            previous = normalize_telegram_users(merchant.telegram_user_ids or [])
            prev_active: Dict[int, bool] = {
                int(p["id"]): bool(p.get("active", False)) for p in previous
            }
            for entry in incoming:
                if not entry.get("active"):
                    entry["active"] = prev_active.get(int(entry["id"]), False)
            updates["telegram_user_ids"] = incoming

        old_values = {k: getattr(merchant, k) for k in updates}
        if trader_ids is not None:
            old_values["trader_ids"] = sorted([t.id for t in (merchant.traders or [])])
        if group_ids is not None:
            old_values["group_ids"] = sorted([g.id for g in (merchant.trader_groups or [])])

        async with self.session.begin_nested():
            if updates:
                merchant = await self.repository.update(merchant_id, updates)

            if trader_ids is not None:
                from sqlalchemy import select
                from app.modules.traders.models import Trader
                if trader_ids:
                    result = await self.session.execute(
                        select(Trader).where(Trader.id.in_(trader_ids))
                    )
                    new_traders = list(result.scalars().all())
                else:
                    new_traders = []
                merchant.traders = new_traders
                await self.session.flush()

            if group_ids is not None:
                from sqlalchemy import select
                from app.modules.traders.models import TraderGroup
                if group_ids:
                    result = await self.session.execute(
                        select(TraderGroup).where(TraderGroup.id.in_(group_ids))
                    )
                    new_groups = list(result.scalars().all())
                else:
                    new_groups = []
                merchant.trader_groups = new_groups
                await self.session.flush()

            if cascade_group_ids is not None:
                from sqlalchemy import select as _sa_select
                from app.modules.cascading.models import CascadeGroup as _CascadeGroup
                if cascade_group_ids:
                    result = await self.session.execute(
                        _sa_select(_CascadeGroup).where(_CascadeGroup.id.in_(cascade_group_ids))
                    )
                    new_cascade_groups = list(result.scalars().all())
                else:
                    new_cascade_groups = []
                merchant.cascade_groups = new_cascade_groups
                await self.session.flush()

            if trader_ids is not None or group_ids is not None or cascade_group_ids is not None:
                refresh_fields = ["traders", "trader_groups"]
                if cascade_group_ids is not None:
                    refresh_fields.append("cascade_groups")
                await self.session.refresh(merchant, refresh_fields)

            new_values = dict(updates)
            if trader_ids is not None:
                new_values["trader_ids"] = sorted([t.id for t in (merchant.traders or [])])
            if group_ids is not None:
                new_values["group_ids"] = sorted([g.id for g in (merchant.trader_groups or [])])
            if cascade_group_ids is not None:
                new_values["cascade_group_ids"] = sorted(
                    [g.id for g in (merchant.cascade_groups or [])]
                )

            await self.audit_log(
                action="update_merchant_settings",
                entity_type="merchant",
                entity_id=merchant_id,
                user_id=user_id,
                old_values=old_values,
                new_values=new_values,
            )

        await merchant_auth_cache.invalidate(merchant_id=merchant_id)
        return merchant

    async def reset_api_key(self, merchant_id: int, admin_user_id: Optional[int] = None) -> Tuple[str, str]:
        """Reset the API keys for a merchant and return the new (api_key, api_secret)."""
        merchant = await self.repository.get(merchant_id)
        if not merchant:
            raise NotFoundException(f"Merchant {merchant_id} not found")

        old_api_key = merchant.api_key
        new_api_key = secrets.token_hex(16)
        new_api_secret = secrets.token_urlsafe(32)
        encrypted_secret = encrypt_api_secret(new_api_secret)

        async with self.session.begin_nested():
            await self.repository.update(merchant_id, {
                "api_key": new_api_key,
                "api_secret": encrypted_secret,
            })
            await self.audit_log(
                action="reset_api_key",
                entity_type="merchant",
                entity_id=merchant_id,
                user_id=admin_user_id,
            )

        await merchant_auth_cache.invalidate(api_key=old_api_key, merchant_id=merchant_id)
        return new_api_key, new_api_secret

    @staticmethod
    def mask_api_key(api_key: str) -> str:
        if len(api_key) <= 12:
            return api_key[:4] + "****"
        return api_key[:8] + "****" + api_key[-4:]

    # ── Bot-facing limits aggregation ──────────────────────────

    async def get_bot_limits_for_tg_user(self, tg_user_id: int):
        """Aggregate payin capacity per (terminal, payment_method) for a TG user.

        For every merchant terminal the TG user owns we walk the trader
        requisites that pooling would consider eligible (active, not archived,
        local source, trader payin enabled, allowed for this merchant via
        direct-bind / group / accept_all_merchants) and return per-method:

          * ``available``        = Σ MIN(daily_room_i, monthly_room_i) per requisite
                                   — i.e. the actual amount that can be pushed
                                   through right now, taking BOTH daily and
                                   monthly caps into account (whichever is
                                   tighter on each requisite). Each requisite's
                                   ``daily_room``/``monthly_room`` is already
                                   capped at 0 with ``GREATEST``, so the result
                                   is non-negative.
          * ``min_amount``       = MIN(limit_min_transaction) — the smallest
                                    per-order minimum any eligible requisite
                                    is configured to accept.
          * ``max_amount``       = MAX over eligible requisites of
                                    LEAST(limit_max_transaction, daily_room,
                                    monthly_room). I.e. the **largest single
                                    order that will actually pass right now**
                                    — bounded both by per-transaction config
                                    AND by the requisite's remaining headroom.
                                    Plain MAX(limit_max_transaction) would lie
                                    when one requisite has a huge config max
                                    but its daily/monthly cap is already eaten.
          * ``concurrent_slots`` = Σ(limit_max_concurrent_orders − active_count)
                                    or ``None`` if any eligible requisite has no
                                    concurrent limit set (treated as unlimited).
          * ``requisites_count`` = number of currently active eligible requisites
                                    contributing (passes every filter above).

        in-flight = Σ ``Order.amount`` for orders in PENDING / RECEIPT_UPLOADED /
        DISPUTED on each requisite. Same denominator as PoolingService uses,
        so the answer matches what the next pooling call would actually see.
        """
        from sqlalchemy import case, func, select

        from app.common.enums.cascading import RequisiteSource
        from app.common.enums.orders import OrderStatus
        from app.common.enums.requisites import RequisiteStatus
        from app.common.enums.traders import TraderStatus
        from app.modules.merchants.schemas import (
            BotLimitsResponse,
            BotMethodLimits,
            BotTerminalLimits,
        )
        from app.modules.orders.models import Order
        from app.modules.pooling.service import PoolingService
        from app.modules.requisites.models import Requisite, RequisiteLimit
        from app.modules.traders.models import Trader

        merchants = await self.repository.list_by_telegram_user_id(tg_user_id)
        terminals: list[BotTerminalLimits] = []

        active_statuses = (
            OrderStatus.PENDING,
            OrderStatus.RECEIPT_UPLOADED,
            OrderStatus.DISPUTED,
        )

        active_orders_subq = (
            select(
                Order.requisite_id,
                func.count(Order.id).label("active_count"),
                func.coalesce(func.sum(Order.amount), 0).label("pending_amount"),
            )
            .where(Order.status.in_(active_statuses))
            .group_by(Order.requisite_id)
            .subquery()
        )

        daily_room = func.greatest(
            RequisiteLimit.limit_daily
            - RequisiteLimit.current_daily_turnover
            - func.coalesce(active_orders_subq.c.pending_amount, 0),
            0,
        )
        monthly_room = func.greatest(
            RequisiteLimit.limit_monthly
            - RequisiteLimit.current_monthly_turnover
            - func.coalesce(active_orders_subq.c.pending_amount, 0),
            0,
        )
        # The actual fiat amount that can be pushed through THIS requisite
        # right now is bounded by whichever of daily / monthly is tighter.
        # Summing this across requisites gives the true "available now"
        # capacity for the pool — using SUM(daily) alone would overstate
        # capacity whenever the monthly cap is the binding constraint.
        per_requisite_available = func.least(daily_room, monthly_room)
        # Largest single-order amount this requisite can still accept right
        # now: bounded by the per-transaction config AND by the remaining
        # daily/monthly room. Taking MAX across eligible requisites gives
        # the largest order the pool will reliably accept — using
        # MAX(limit_max_transaction) alone would lie when the requisite
        # with the high config cap is already drained.
        per_requisite_max_single_now = func.least(
            RequisiteLimit.limit_max_transaction,
            daily_room,
            monthly_room,
        )
        # Per-requisite slot count: free slots when a limit is set, NULL when
        # unlimited (so SUM ignores it and ``bool_or(... IS NULL)`` flags
        # the bucket as unlimited downstream).
        slot_room = case(
            (
                RequisiteLimit.limit_max_concurrent_orders.is_(None),
                None,
            ),
            else_=func.greatest(
                RequisiteLimit.limit_max_concurrent_orders
                - func.coalesce(active_orders_subq.c.active_count, 0),
                0,
            ),
        )

        for m in merchants:
            stmt = (
                select(
                    Requisite.payment_method.label("payment_method"),
                    Requisite.currency.label("currency"),
                    func.coalesce(func.sum(per_requisite_available), 0).label("available"),
                    func.min(RequisiteLimit.limit_min_transaction).label("min_amount"),
                    func.coalesce(
                        func.max(per_requisite_max_single_now), 0
                    ).label("max_amount"),
                    func.sum(slot_room).label("slots_sum"),
                    func.bool_or(
                        RequisiteLimit.limit_max_concurrent_orders.is_(None)
                    ).label("any_unlimited"),
                    func.count(Requisite.id).label("requisites_count"),
                )
                .join(RequisiteLimit, Requisite.id == RequisiteLimit.requisite_id)
                .join(Trader, Trader.user_id == Requisite.trader_id)
                .outerjoin(
                    active_orders_subq,
                    Requisite.id == active_orders_subq.c.requisite_id,
                )
                .where(
                    Requisite.is_active.is_(True),
                    Requisite.is_archived.is_(False),
                    Requisite.source == RequisiteSource.LOCAL,
                    Requisite.status == RequisiteStatus.ENABLED,
                    Trader.status == TraderStatus.ENABLED,
                    Trader.is_payin_active.is_(True),
                    PoolingService._merchant_allowed_trader_clause(m.id),
                )
                .group_by(Requisite.payment_method, Requisite.currency)
                .order_by(Requisite.payment_method, Requisite.currency)
            )

            rows = (await self.session.execute(stmt)).all()
            methods: list[BotMethodLimits] = []
            for row in rows:
                pm = row.payment_method
                cur = row.currency
                concurrent_slots: Optional[int]
                if row.any_unlimited:
                    concurrent_slots = None
                else:
                    concurrent_slots = (
                        int(row.slots_sum) if row.slots_sum is not None else 0
                    )

                methods.append(
                    BotMethodLimits(
                        payment_method=pm.value if hasattr(pm, "value") else str(pm),
                        currency=cur.value if hasattr(cur, "value") else str(cur),
                        available=float(row.available or 0),
                        min_amount=float(row.min_amount or 0),
                        max_amount=float(row.max_amount or 0),
                        concurrent_slots=concurrent_slots,
                        requisites_count=int(row.requisites_count or 0),
                    )
                )

            terminals.append(
                BotTerminalLimits(
                    id=m.id,
                    name=m.name,
                    currency=m.currency.value
                    if hasattr(m.currency, "value")
                    else str(m.currency),
                    methods=methods,
                )
            )

        return BotLimitsResponse(terminals=terminals)
