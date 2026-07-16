import random
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, exists, func, literal, or_, select
from sqlalchemy.orm import Load, raiseload
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.cascading import RequisiteSource
from app.common.enums.orders import OrderStatus
from app.common.enums.pooling import PoolingStrategy
from app.common.enums.requisites import RequisiteStatus
from app.common.enums.traders import TraderStatus
from app.modules.orders.models import Order
from app.modules.requisites.models import Requisite, RequisiteLimit
from app.modules.traders.models import (
    Trader,
    merchant_trader_groups,
    trader_group_members,
    trader_merchants,
)


def _merge_intervals(intervals: List[tuple]) -> List[Dict[str, Decimal]]:
    """Merge a list of ``(floor, ceiling)`` tuples into the minimum set of
    disjoint ``{"min", "max"}`` ranges, sorted ascending by ``min``.

    Two intervals are merged when the next one starts on or before the current
    one's end (so ``[100,200]`` + ``[200,300]`` collapse into ``[100,300]``).
    Truly disjoint reqs stay separate — that is the whole point: the gap
    between ``[4000,4500]`` and ``[5000,10000]`` is the "no requisite" zone
    we want to surface to the merchant.
    """
    if not intervals:
        return []
    sorted_iv = sorted(intervals, key=lambda iv: iv[0])
    out: List[Dict[str, Decimal]] = []
    cur_lo, cur_hi = sorted_iv[0]
    for lo, hi in sorted_iv[1:]:
        if lo <= cur_hi:
            if hi > cur_hi:
                cur_hi = hi
        else:
            out.append({"min": cur_lo, "max": cur_hi})
            cur_lo, cur_hi = lo, hi
    out.append({"min": cur_lo, "max": cur_hi})
    return out


@dataclass
class PoolingResult:
    selected: Optional[Requisite] = None
    candidates: List[Dict[str, Any]] = field(default_factory=list)


class PoolingService:
    """
    Service responsible for selecting the best available requisite for an order
    based on various strategies (random, least recently used, etc).
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _merchant_allowed_trader_clause(merchant_id: int):
        """Build a SQLAlchemy clause that restricts Trader rows to those allowed
        to serve the given merchant.

        Allowed iff:
          - trader is directly bound to the merchant (trader_merchants), OR
          - trader and merchant share a TraderGroup, OR
          - merchant has no direct/group bindings AND trader.accept_all_merchants is True.
        """
        direct_allowed = select(trader_merchants.c.trader_id).where(
            trader_merchants.c.merchant_id == merchant_id
        )
        via_group_allowed = (
            select(trader_group_members.c.trader_id)
            .join(
                merchant_trader_groups,
                merchant_trader_groups.c.group_id == trader_group_members.c.group_id,
            )
            .where(merchant_trader_groups.c.merchant_id == merchant_id)
        )
        allowed_trader_ids = direct_allowed.union(via_group_allowed)

        merchant_has_direct = exists(
            select(literal(1)).where(trader_merchants.c.merchant_id == merchant_id)
        )
        merchant_has_group = exists(
            select(literal(1)).where(merchant_trader_groups.c.merchant_id == merchant_id)
        )

        return or_(
            Trader.id.in_(allowed_trader_ids),
            and_(
                ~merchant_has_direct,
                ~merchant_has_group,
                Trader.accept_all_merchants == True,  # noqa: E712
            ),
        )

    def _get_base_query(self, order: Order):
        """
        Build the base query for available requisites matching the order criteria.
        Pending order amounts are included in the daily/monthly limit check so that
        the available capacity decreases as soon as an order is opened and returns
        when it is cancelled or failed.
        """
        from app.modules.finance.models import Balance
        from app.common.enums.balances import BalanceType
        from app.common.enums.finances import Currency as FinanceCurrency

        _active_statuses = [OrderStatus.PENDING, OrderStatus.RECEIPT_UPLOADED, OrderStatus.DISPUTED]

        active_orders_subq = (
            select(
                Order.requisite_id,
                func.count(Order.id).label("active_count"),
                func.coalesce(func.sum(Order.amount), 0).label("pending_amount"),
            )
            .where(
                Order.status.in_(_active_statuses),
                Order.currency == order.currency,
            )
            .group_by(Order.requisite_id)
            .subquery()
        )

        stmt = select(Requisite).join(RequisiteLimit).join(
            Trader, Trader.user_id == Requisite.trader_id
        ).outerjoin(
            active_orders_subq, Requisite.id == active_orders_subq.c.requisite_id
        ).outerjoin(
            Balance,
            (Balance.user_id == Requisite.trader_id) &
            (Balance.type == BalanceType.WORK) &
            (Balance.currency == FinanceCurrency.USDT)
        ).where(
            Requisite.is_active == True,
            Requisite.is_archived == False,
            Requisite.source == RequisiteSource.LOCAL,
            Requisite.status == RequisiteStatus.ENABLED,
            Trader.status == TraderStatus.ENABLED,
            Trader.is_payin_active == True,
            Requisite.currency == order.currency,
            Requisite.payment_method == order.payment_method,
            RequisiteLimit.limit_min_transaction <= order.amount,
            RequisiteLimit.limit_max_transaction >= order.amount,
            # Include already-pending amounts so the limit is "reserved" for active orders
            (RequisiteLimit.current_daily_turnover
             + func.coalesce(active_orders_subq.c.pending_amount, 0)
             + order.amount) <= RequisiteLimit.limit_daily,
            (RequisiteLimit.current_monthly_turnover
             + func.coalesce(active_orders_subq.c.pending_amount, 0)
             + order.amount) <= RequisiteLimit.limit_monthly,
            (RequisiteLimit.limit_max_concurrent_orders.is_(None)) |
            (func.coalesce(active_orders_subq.c.active_count, 0) < RequisiteLimit.limit_max_concurrent_orders),
            func.coalesce(Balance.amount, 0) >= order.amount_usdt
        )

        # Strict filter by payment option when the order specifies a target bank
        if order.payment_option_id is not None:
            stmt = stmt.where(Requisite.payment_option_id == order.payment_option_id)

        # Restrict to traders allowed to serve this merchant.
        if order.merchant_id is not None:
            stmt = stmt.where(self._merchant_allowed_trader_clause(order.merchant_id))

        return stmt

    async def select_requisite(
        self, order: Order, strategy: PoolingStrategy = PoolingStrategy.RANDOM
    ) -> Optional[Requisite]:
        stmt = self._get_base_query(order)

        if strategy == PoolingStrategy.WEIGHTED:
            requisites = (await self.session.execute(stmt)).scalars().all()
            if not requisites:
                return None
            weights = [float(r.priority_score) for r in requisites]
            if sum(weights) > 0:
                return random.choices(requisites, weights=weights, k=1)[0]
            return random.choice(requisites)

        elif strategy == PoolingStrategy.RANDOM:
            result = await self.session.execute(stmt)
            requisites = result.scalars().all()
            if not requisites:
                return None
            return random.choice(requisites)

        elif strategy == PoolingStrategy.LEAST_RECENTLY_USED:
            stmt = stmt.order_by(Requisite.last_used_at.asc().nulls_first())
            result = await self.session.execute(stmt)
            return result.scalars().first()

        elif strategy == PoolingStrategy.BANDIT:
            result = await self.session.execute(stmt)
            requisites = result.scalars().all()
            if not requisites:
                return None
            return await self._select_via_bandit(order, requisites)

        return None

    async def _bandit_choose_trader(
        self, order: Order, trader_ids: list[str]
    ) -> Optional[str]:
        """Ask the selector engine to pick a trader id from ``trader_ids``.

        Returns the chosen trader id, or ``None`` to signal "fall back" —
        whenever the selector isn't wired up, can't decide, or its storage is
        unavailable. The order pipeline must never stall on a misconfigured /
        failing selector, so every caller treats ``None`` as "pick randomly".
        """
        from app.modules.selector import bootstrap as selector_bootstrap
        from app.modules.selector.core.selector import SelectionContext
        from app.modules.selector import metrics as prom

        registry = selector_bootstrap.get_registry()
        if registry is None or "traders" not in registry.names():
            return None
        if not trader_ids:
            return None

        ctx = SelectionContext(
            order_id=str(getattr(order, "id", "") or ""),
            amount=float(order.amount) if order.amount is not None else None,
            currency=(
                order.currency.value if hasattr(order.currency, "value") else order.currency
            ),
        )
        try:
            sel = registry.get("traders")
            result = await sel.select(trader_ids, ctx)
        except Exception:
            prom.fallback_total.labels(name="traders", reason="select_exception").inc()
            return None
        if result.entity_id is None or result.entity_id not in trader_ids:
            prom.fallback_total.labels(name="traders", reason="no_selection").inc()
            return None
        return result.entity_id

    async def _select_via_bandit(
        self, order: Order, requisites: list[Requisite]
    ) -> Optional[Requisite]:
        """Bandit-select a Requisite (used by ``select_requisite``).

        Groups the eligible requisites by trader, asks the selector to pick a
        trader, then chooses one of that trader's requisites uniformly. Any
        "fall back" signal → ``random.choice`` over all eligible requisites.
        """
        by_trader: dict[str, list[Requisite]] = {}
        for r in requisites:
            by_trader.setdefault(str(r.trader_id), []).append(r)

        chosen_trader = await self._bandit_choose_trader(order, list(by_trader.keys()))
        if chosen_trader is None or chosen_trader not in by_trader:
            return random.choice(requisites)
        return random.choice(by_trader[chosen_trader])

    async def _bandit_pick_candidate(
        self, order: Order, candidates: list[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Bandit-select among diagnostic candidate dicts (keyed by trader via
        ``user_id``). Same trader-pooled model as ``_select_via_bandit`` but
        for the dict-shaped candidates the diagnostics path carries. Falls
        back to ``random.choice`` over all candidates.
        """
        by_trader: dict[str, list[Dict[str, Any]]] = {}
        for c in candidates:
            by_trader.setdefault(str(c.get("user_id")), []).append(c)

        chosen_trader = await self._bandit_choose_trader(order, list(by_trader.keys()))
        if chosen_trader is None or chosen_trader not in by_trader:
            return random.choice(candidates)
        return random.choice(by_trader[chosen_trader])

    async def _resolve_merchant_access(self, merchant_id: Optional[int]) -> tuple[Optional[set[int]], bool]:
        """Return (allowed_trader_ids, merchant_has_bindings) for the merchant.

        allowed_trader_ids — trader.id values directly bound or bound via shared groups.
                             None when merchant_id is None (filter is skipped entirely).
        merchant_has_bindings — True iff the merchant has any direct or group binding.
        """
        if merchant_id is None:
            return None, False

        direct = await self.session.execute(
            select(trader_merchants.c.trader_id).where(
                trader_merchants.c.merchant_id == merchant_id
            )
        )
        via_groups = await self.session.execute(
            select(trader_group_members.c.trader_id)
            .join(
                merchant_trader_groups,
                merchant_trader_groups.c.group_id == trader_group_members.c.group_id,
            )
            .where(merchant_trader_groups.c.merchant_id == merchant_id)
        )
        direct_ids = {row[0] for row in direct.all()}
        group_ids = {row[0] for row in via_groups.all()}
        allowed = direct_ids | group_ids

        has_direct = bool(direct_ids)
        has_group_binding_q = await self.session.execute(
            select(literal(1)).where(merchant_trader_groups.c.merchant_id == merchant_id).limit(1)
        )
        has_group_binding = has_group_binding_q.first() is not None

        return allowed, (has_direct or has_group_binding)

    async def select_requisite_with_diagnostics(
        self, order: Order, strategy: PoolingStrategy = PoolingStrategy.LEAST_RECENTLY_USED
    ) -> PoolingResult:
        """
        Same selection logic but also returns diagnostic info:
        which requisites were candidates and which were excluded (with reasons).
        """
        amount = Decimal(str(order.amount))

        from app.modules.finance.models import Balance
        from app.common.enums.balances import BalanceType
        from app.common.enums.finances import Currency as FinanceCurrency

        _active_statuses = [OrderStatus.PENDING, OrderStatus.RECEIPT_UPLOADED, OrderStatus.DISPUTED]

        active_orders_subq = (
            select(
                Order.requisite_id,
                func.count(Order.id).label("active_count"),
                func.coalesce(func.sum(Order.amount), 0).label("pending_amount"),
            )
            .where(
                Order.status.in_(_active_statuses),
                Order.currency == order.currency,
            )
            .group_by(Order.requisite_id)
            .subquery()
        )

        stmt = (
            select(
                Requisite,
                RequisiteLimit,
                func.coalesce(active_orders_subq.c.active_count, 0).label("active_count"),
                func.coalesce(Balance.amount, 0).label("trader_balance"),
                func.coalesce(active_orders_subq.c.pending_amount, 0).label("pending_amount"),
                Trader,
            )
            .join(RequisiteLimit, RequisiteLimit.requisite_id == Requisite.id)
            .join(Trader, Trader.user_id == Requisite.trader_id)
            .outerjoin(active_orders_subq, Requisite.id == active_orders_subq.c.requisite_id)
            .outerjoin(
                Balance,
                (Balance.user_id == Requisite.trader_id) &
                (Balance.type == BalanceType.WORK) &
                (Balance.currency == FinanceCurrency.USDT)
            )
            .where(
                Requisite.currency == order.currency,
                Requisite.payment_method == order.payment_method,
                Requisite.source == RequisiteSource.LOCAL,
                Requisite.is_active.is_(True),
                Requisite.is_archived.is_(False),
                Requisite.status == RequisiteStatus.ENABLED,
                Trader.status == TraderStatus.ENABLED,
                Trader.is_payin_active.is_(True),
            )
            .options(
                Load(Requisite).raiseload("*"),
                Load(Trader).raiseload("*"),
            )
        )

        if order.merchant_id is not None:
            stmt = stmt.where(self._merchant_allowed_trader_clause(order.merchant_id))

        result = await self.session.execute(stmt)
        rows = result.all()

        candidates: List[Dict[str, Any]] = []

        for req, limits, active_count, trader_balance, pending_amount, trader in rows:
            info = self._serialize_requisite(req, limits, int(active_count), Decimal(str(pending_amount)))
            reason = self._check_exclusion(
                req, limits, amount, int(active_count),
                Decimal(str(trader_balance)), Decimal(str(order.amount_usdt)),
                Decimal(str(pending_amount)),
                trader=trader,
                order_payment_option_id=order.payment_option_id,
            )
            if not reason:
                candidates.append(info)

        selected = None
        if candidates:
            if strategy == PoolingStrategy.WEIGHTED:
                weights = [c["priority_score"] for c in candidates]
                pick = (random.choices(candidates, weights=weights, k=1)[0]
                        if sum(weights) > 0 else random.choice(candidates))
            elif strategy == PoolingStrategy.RANDOM:
                pick = random.choice(candidates)
            elif strategy == PoolingStrategy.BANDIT:
                pick = await self._bandit_pick_candidate(order, candidates)
            else:
                candidates.sort(key=lambda c: c.get("last_used_at") or "")
                pick = candidates[0]

            pick_id = pick["id"]
            for req, _, _, _, _, _ in rows:
                if req.id == pick_id:
                    selected = req
                    break

        return PoolingResult(selected=selected, candidates=candidates)

    @staticmethod
    def _serialize_requisite(
        req: Requisite,
        limits: Optional[RequisiteLimit],
        active_count: int,
        pending_amount: Decimal = Decimal("0"),
    ) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": req.id,
            "user_id": req.trader_id,
            "bank_name": req.bank_name,
            "payment_method": req.payment_method.value if req.payment_method else None,
            "status": req.status.value if req.status else None,
            "is_active": req.is_active,
            "is_archived": req.is_archived,
            "currency": req.currency.value if req.currency else None,
            "last_used_at": req.last_used_at.isoformat() if req.last_used_at else None,
            "active_orders": active_count,
            "priority_score": float(req.priority_score),
        }
        if limits:
            data["limits"] = {
                "limit_min_transaction": float(limits.limit_min_transaction),
                "limit_max_transaction": float(limits.limit_max_transaction),
                "limit_daily": float(limits.limit_daily),
                "limit_monthly": float(limits.limit_monthly),
                "limit_max_concurrent_orders": limits.limit_max_concurrent_orders,
                "current_daily_turnover": float(limits.current_daily_turnover),
                "current_monthly_turnover": float(limits.current_monthly_turnover),
                # Available headroom already reduced by pending (not yet confirmed) orders
                "pending_amount": float(pending_amount),
                "available_daily": float(
                    limits.limit_daily - limits.current_daily_turnover - pending_amount
                ),
                "available_monthly": float(
                    limits.limit_monthly - limits.current_monthly_turnover - pending_amount
                ),
            }
        else:
            data["limits"] = None
        return data

    @staticmethod
    def _check_exclusion(
        req: Requisite,
        limits: Optional[RequisiteLimit],
        amount: Decimal,
        active_count: int,
        trader_balance: Decimal = Decimal("0"),
        amount_usdt: Decimal = Decimal("0"),
        pending_amount: Decimal = Decimal("0"),
        trader: Optional[Trader] = None,
        order_payment_option_id: Optional[int] = None,
        allowed_trader_ids: Optional[set] = None,
        merchant_has_bindings: bool = False,
    ) -> Optional[str]:
        """Return the first exclusion reason, or None if the requisite is eligible."""
        if not req.is_active:
            return "is_active=false"
        if req.is_archived:
            return "is_archived=true"
        if req.status != RequisiteStatus.ENABLED:
            return f"status={req.status.value}"
        if trader is None:
            return "trader_profile_missing"
        if trader.status != TraderStatus.ENABLED:
            return f"trader_status={trader.status.value}"
        if not trader.is_payin_active:
            return "trader_payin_inactive"
        if allowed_trader_ids is not None:
            in_allowed = trader.id in allowed_trader_ids
            if not in_allowed:
                if merchant_has_bindings:
                    return "not_assigned_to_merchant"
                if not getattr(trader, "accept_all_merchants", False):
                    return "trader_not_open_to_unassigned_merchants"
        if trader_balance < amount_usdt:
            return f"insufficient_balance ({float(trader_balance)} < {float(amount_usdt)})"
        if (
            order_payment_option_id is not None
            and req.payment_option_id != order_payment_option_id
        ):
            return f"payment_option_mismatch (req={req.payment_option_id}, order={order_payment_option_id})"
        if not limits:
            return "no_limits_configured"
        if limits.limit_min_transaction > amount:
            return f"amount_below_min ({float(amount)} < {float(limits.limit_min_transaction)})"
        if limits.limit_max_transaction < amount:
            return f"amount_above_max ({float(amount)} > {float(limits.limit_max_transaction)})"
        if (limits.current_daily_turnover + pending_amount + amount) > limits.limit_daily:
            effective = float(limits.current_daily_turnover + pending_amount + amount)
            return f"daily_limit_exceeded ({effective} > {float(limits.limit_daily)})"
        if (limits.current_monthly_turnover + pending_amount + amount) > limits.limit_monthly:
            effective = float(limits.current_monthly_turnover + pending_amount + amount)
            return f"monthly_limit_exceeded ({effective} > {float(limits.limit_monthly)})"
        if limits.limit_max_concurrent_orders is not None and active_count >= limits.limit_max_concurrent_orders:
            return f"concurrent_limit ({active_count} >= {limits.limit_max_concurrent_orders})"
        return None

    async def compute_method_limits(
        self,
        *,
        merchant_id: int,
        currency,
        payment_method,
        rate: Decimal,
    ) -> List[Dict[str, Decimal]]:
        """Discrete fiat amount ranges the platform can accept right now for
        ``(currency, payment_method)`` scoped to one merchant's ACL.

        Mirrors the canonical pooling gates (``_get_base_query``) WITHOUT a
        specific amount. Per requisite the effective interval is
        ``[floor, ceiling]`` where:
          * floor   = ``limit_min_transaction``
          * ceiling = LEAST(``limit_max_transaction``, daily remaining,
                            monthly remaining, balance_usdt × rate)
        Reqs whose ceiling < floor are dropped (they can't accept any amount).
        The surviving intervals are merged in Python: overlapping or touching
        ranges collapse into one (``[4000,4500]+[4200,5000] → [4000,5000]``),
        and discrete reqs stay separate (``[4000,4500] · [5000,10000]`` →
        TWO entries, exposing the gap ``[4500,5000]`` where no requisite can
        accept). Returned list is sorted ascending by min; empty when nothing
        can accept anything.
        """
        from app.modules.finance.models import Balance
        from app.common.enums.balances import BalanceType
        from app.common.enums.finances import Currency as FinanceCurrency

        _active_statuses = [OrderStatus.PENDING, OrderStatus.RECEIPT_UPLOADED, OrderStatus.DISPUTED]

        # Pending fiat amount already reserved per requisite — same shape pooling
        # uses to ensure the limit math stays consistent ("the row decrements as
        # soon as an order opens, returns when it cancels").
        active_orders_subq = (
            select(
                Order.requisite_id,
                func.count(Order.id).label("active_count"),
                func.coalesce(func.sum(Order.amount), 0).label("pending_amount"),
            )
            .where(
                Order.status.in_(_active_statuses),
                Order.currency == currency,
            )
            .group_by(Order.requisite_id)
            .subquery()
        )

        # Per-requisite effective ceiling — the SAME LEAST() the order-time
        # gate would impose, only without a specific amount.
        ceiling_expr = func.least(
            RequisiteLimit.limit_max_transaction,
            RequisiteLimit.limit_daily
                - RequisiteLimit.current_daily_turnover
                - func.coalesce(active_orders_subq.c.pending_amount, 0),
            RequisiteLimit.limit_monthly
                - RequisiteLimit.current_monthly_turnover
                - func.coalesce(active_orders_subq.c.pending_amount, 0),
            func.coalesce(Balance.amount, 0) * literal(rate),
        )

        stmt = (
            select(
                RequisiteLimit.limit_min_transaction.label("floor"),
                ceiling_expr.label("ceiling"),
            )
            .select_from(Requisite)
            .join(RequisiteLimit)
            .join(Trader, Trader.user_id == Requisite.trader_id)
            .outerjoin(active_orders_subq, Requisite.id == active_orders_subq.c.requisite_id)
            .outerjoin(
                Balance,
                (Balance.user_id == Requisite.trader_id)
                & (Balance.type == BalanceType.WORK)
                & (Balance.currency == FinanceCurrency.USDT),
            )
            .where(
                Requisite.is_active == True,                      # noqa: E712
                Requisite.is_archived == False,                   # noqa: E712
                Requisite.source == RequisiteSource.LOCAL,
                Requisite.status == RequisiteStatus.ENABLED,
                Trader.status == TraderStatus.ENABLED,
                Trader.is_payin_active == True,                   # noqa: E712
                Requisite.currency == currency,
                Requisite.payment_method == payment_method,
                func.coalesce(Balance.amount, 0) > 0,
                or_(
                    RequisiteLimit.limit_max_concurrent_orders.is_(None),
                    func.coalesce(active_orders_subq.c.active_count, 0)
                        < RequisiteLimit.limit_max_concurrent_orders,
                ),
                # Drop requisites with no viable range at all (ceiling < floor)
                # — they can't accept any amount.
                ceiling_expr >= RequisiteLimit.limit_min_transaction,
                self._merchant_allowed_trader_clause(merchant_id),
            )
            .order_by(RequisiteLimit.limit_min_transaction.asc())
        )
        rows = (await self.session.execute(stmt)).all()
        intervals = [
            (Decimal(r.floor), Decimal(r.ceiling))
            for r in rows
            if r.floor is not None and r.ceiling is not None
        ]
        return _merge_intervals(intervals)
