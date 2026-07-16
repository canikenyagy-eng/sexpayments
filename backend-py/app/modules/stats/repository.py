from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import Integer, case, cast, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.balances import LedgerReferenceType
from app.common.enums.cascading import RequisiteSource
from app.common.enums.disputes import DisputeStatus
from app.common.enums.finances import WithdrawalStatus
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection
from app.common.enums.receipt_checks import ReceiptCheckStatus, ReceiptCheckTrigger
from app.common.enums.requisites import RequisiteStatus
from app.common.enums.traders import TraderStatus
from app.common.types import utcnow
from app.common.enums.users import UserRole
from app.modules.audit import repository as ch_logs
from app.modules.disputes.models import Dispute
from app.modules.finance.models import LedgerEntry, WithdrawalRequest
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.receipt_checks.models import ReceiptCheck, ReceiptCheckProvider
from app.modules.requisites.models import Requisite, RequisiteLimit
from app.modules.stats.models import MerchantStatsSnapshot, StatsSnapshot
from app.modules.traders.models import Trader

ACTIVE_ORDER_STATUSES = [
    OrderStatus.CREATED, OrderStatus.PENDING, OrderStatus.RECEIPT_UPLOADED,
]
ACTIVE_DISPUTE_STATUSES = [
    DisputeStatus.OPEN,
]


@dataclass
class TimeseriesPoint:
    """Single bucket of an admin time-series chart."""
    ts: datetime
    turnover_usdt: float
    profit_usdt: float
    orders: int
    # Turnover slice that came from cascade providers (orders with a
    # ``won`` cascade_order_attempts row). Lets the dashboard split the
    # total bar into «local trader» and «provider» portions.
    provider_turnover_usdt: float = 0.0
    # Platform-profit slice from those same cascade orders (Σ system_profit
    # over orders with a ``won`` attempt) — the cascade subset of profit_usdt.
    provider_profit_usdt: float = 0.0
    provider_orders: int = 0


@dataclass
class OrderAggregates:
    turnover_usdt: float
    # Net platform profit in USDT: Σ(fee_usdt − trader_fee_usdt) across
    # SUCCESS orders, minus net teamlead rewards paid for those orders,
    # plus долив margin (Σ doliv_price_usdt − trader_fee_usdt) from COMPLETED
    # доливы — the price − reward residual that lands on the system balance.
    profit_usdt: float
    requests_rub: float
    orders_total: int
    orders_success: int
    orders_active: int
    payout_count: int


@dataclass
class NewOrdersDelta:
    total: int
    success: int
    payouts: int
    turnover: float
    profit: float
    requests: float


@dataclass
class UpdatedOrdersDelta:
    new_successes: int
    turnover: float
    profit: float


@dataclass
class RealtimeMetrics:
    merchants_online_24h: int
    traders_online_24h: int
    pending_withdrawals: int
    active_disputes: int
    orders_active: int
    payin_requests_24h: int
    requests_rub_24h: float


class StatsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def _strip_tz(dt: Optional[datetime]) -> Optional[datetime]:
        if dt and dt.tzinfo:
            return dt.replace(tzinfo=None)
        return dt

    @staticmethod
    def _date_conditions(
        date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> List:
        conds: List = []
        if date_from:
            conds.append(Order.created_at >= StatsRepository._strip_tz(date_from))
        if date_to:
            conds.append(Order.created_at <= StatsRepository._strip_tz(date_to))
        return conds

    @staticmethod
    def _system_profit_expr():
        """Per-order gross margin of the platform before teamlead payouts.

        fee_usdt is the commission the merchant pays to the system; trader_fee_usdt
        is what the system pays the trader out of that commission. The residual is
        the platform's share before teamlead rewards are distributed.
        """
        return func.coalesce(Order.fee_usdt, 0) - func.coalesce(
            Order.trader_fee_usdt, 0
        )

    @staticmethod
    def _teamlead_reward_signed_amount():
        """Reversal entries (`..._reversal`) cancel an earlier payout, so they
        flow back into the system balance and must be subtracted from the total
        paid out."""
        return case(
            (LedgerEntry.reference_id.like("%_reversal"), -LedgerEntry.amount),
            else_=LedgerEntry.amount,
        )

    @staticmethod
    def _teamlead_reward_order_id():
        """Numeric order id prefix stored in LedgerEntry.reference_id for
        TEAMLEAD_REWARD entries (see teamleaders.service.calculate_and_pay_rewards)."""
        return cast(func.split_part(LedgerEntry.reference_id, "_", 1), Integer)

    async def _sum_teamlead_rewards_for_success(
        self, *order_conditions,
    ) -> float:
        """Sum of net teamlead rewards attributed to SUCCESS orders that match
        the supplied conditions (typically a date window or `Order.created_at`
        cutoff). Reversals are subtracted so the result reflects the actual
        cash outflow to teamleads."""
        stmt = (
            select(
                func.coalesce(
                    func.sum(self._teamlead_reward_signed_amount()), 0
                )
            )
            .select_from(LedgerEntry)
            .join(Order, Order.id == self._teamlead_reward_order_id())
            .where(
                LedgerEntry.reference_type == LedgerReferenceType.TEAMLEAD_REWARD,
                Order.status == OrderStatus.SUCCESS,
                *order_conditions,
            )
        )
        return float((await self.session.execute(stmt)).scalar_one())

    async def _doliv_margin_usdt(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> float:
        """Net platform margin from COMPLETED доливы in the window:
        ``Σ(doliv_price_usdt − trader_fee_usdt)``.

        A долив settle moves the price the requester pays → system
        (``SYSTEM_COMMISSION``) and the доливщик reward system → executor
        (``TRADER_REWARD``); the residual ``price − reward`` is real platform
        profit sitting on the system balance. Доливы are ``Payout`` rows, not
        ``Order``s, so they're absent from the order-based profit aggregation —
        fold this in so the dashboard reflects total profit. Equivalent to
        ``Σ SYSTEM_COMMISSION − TRADER_REWARD`` over ``reference_id LIKE
        'doliv:%'``, but read from the payout columns (consistent with the
        order-profit expr) and self-correcting: a долив pushed out of COMPLETED
        by the admin state machine drops from the sum automatically."""
        from app.common.enums.payouts import PayoutStatus
        from app.modules.payouts.models import Payout

        conds: List = []
        if date_from:
            conds.append(Payout.created_at >= self._strip_tz(date_from))
        if date_to:
            conds.append(Payout.created_at <= self._strip_tz(date_to))
        stmt = select(
            func.coalesce(
                func.sum(
                    func.coalesce(Payout.doliv_price_usdt, 0)
                    - func.coalesce(Payout.trader_fee_usdt, 0)
                ),
                0,
            )
        ).where(
            Payout.is_doliv.is_(True),
            Payout.status == PayoutStatus.COMPLETED,
            *conds,
        )
        return float((await self.session.execute(stmt)).scalar_one())

    async def sum_doliv_margin_since(self, cutoff: datetime) -> float:
        """долив margin (Σ price − reward) for доливы that became COMPLETED
        after ``cutoff`` — the real-time delta layered over the snapshot
        (which already baked in доливы COMPLETED at refresh time via
        ``_doliv_margin_usdt``). Mirrors the order ``new``/``upd`` profit
        deltas so the cached dashboard reflects долив profit live, not only at
        the 10-min snapshot refresh.

        Filters on ``completed_at`` so a долив COMPLETED before the snapshot
        (already counted) isn't double-counted; a долив the admin pushes back
        out of COMPLETED has its ``completed_at`` cleared and self-heals at the
        next snapshot refresh."""
        from app.common.enums.payouts import PayoutStatus
        from app.modules.payouts.models import Payout

        stmt = select(
            func.coalesce(
                func.sum(
                    func.coalesce(Payout.doliv_price_usdt, 0)
                    - func.coalesce(Payout.trader_fee_usdt, 0)
                ),
                0,
            )
        ).where(
            Payout.is_doliv.is_(True),
            Payout.status == PayoutStatus.COMPLETED,
            Payout.completed_at > self._strip_tz(cutoff),
        )
        return float((await self.session.execute(stmt)).scalar_one())

    async def get_order_aggregates(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> OrderAggregates:
        dc = self._date_conditions(date_from, date_to)

        turnover_usdt = float(
            (await self.session.execute(
                select(func.coalesce(func.sum(Order.amount_usdt), 0)).where(
                    Order.status == OrderStatus.SUCCESS, *dc
                )
            )).scalar_one()
        )
        gross_profit_usdt = float(
            (await self.session.execute(
                select(
                    func.coalesce(func.sum(self._system_profit_expr()), 0)
                ).where(Order.status == OrderStatus.SUCCESS, *dc)
            )).scalar_one()
        )
        teamlead_rewards_usdt = await self._sum_teamlead_rewards_for_success(*dc)
        doliv_margin_usdt = await self._doliv_margin_usdt(date_from, date_to)
        profit_usdt = gross_profit_usdt - teamlead_rewards_usdt + doliv_margin_usdt
        requests_rub = float(
            (await self.session.execute(
                select(func.coalesce(func.sum(Order.amount), 0)).where(*dc)
            )).scalar_one()
        )
        orders_total: int = (await self.session.execute(
            select(func.count(Order.id)).where(*dc)
        )).scalar_one()
        orders_success: int = (await self.session.execute(
            select(func.count(Order.id)).where(
                Order.status == OrderStatus.SUCCESS, *dc
            )
        )).scalar_one()
        orders_active: int = (await self.session.execute(
            select(func.count(Order.id)).where(
                Order.status.in_(ACTIVE_ORDER_STATUSES), *dc
            )
        )).scalar_one()
        payout_count: int = (await self.session.execute(
            select(func.count(Order.id)).where(
                Order.direction == PaymentDirection.PAYOUT, *dc
            )
        )).scalar_one()

        return OrderAggregates(
            turnover_usdt=turnover_usdt,
            profit_usdt=profit_usdt,
            requests_rub=requests_rub,
            orders_total=orders_total,
            orders_success=orders_success,
            orders_active=orders_active,
            payout_count=payout_count,
        )

    async def get_admin_timeseries(
        self,
        date_from: Optional[datetime],
        date_to: Optional[datetime],
        granularity: str,
    ) -> List[TimeseriesPoint]:
        """Bucket SUCCESS orders by ``date_trunc(granularity, created_at)`` and
        return turnover (Σ amount_usdt), gross platform profit
        (Σ fee_usdt − trader_fee_usdt) and order count per bucket.

        ``granularity`` must be one of: ``3hour``, ``hour``, ``day``, ``week``,
        ``month``. Teamlead rewards are *not* subtracted here — the chart shows
        gross platform profit; the dashboard cards keep the precise net figure.
        """
        if granularity not in {"3hour", "hour", "day", "week", "month"}:
            raise ValueError(f"unsupported granularity: {granularity}")

        from app.modules.cascading.models import CascadeOrderAttempt
        from app.common.enums.cascading import CascadeAttemptStatus

        if granularity == "3hour":
            bucket = func.date_bin(
                text("interval '3 hours'"),
                Order.created_at,
                text("timestamptz '1970-01-01 00:00:00+00'"),
            ).label("bucket")
        else:
            bucket = func.date_trunc(granularity, Order.created_at).label("bucket")
        dc = self._date_conditions(date_from, date_to)

        # Cascade-routed slice — sum amount_usdt only when the order has a
        # ``won`` cascade_order_attempts row. EXISTS keeps it as a single
        # correlated subquery, no GROUP BY explosion from a JOIN.
        from sqlalchemy import case, exists, literal
        cascade_marker = exists(
            select(literal(1)).where(
                CascadeOrderAttempt.order_id == Order.id,
                CascadeOrderAttempt.status == CascadeAttemptStatus.WON,
            )
        ).correlate(Order)

        provider_turnover_expr = func.coalesce(
            func.sum(case((cascade_marker, Order.amount_usdt), else_=0)), 0,
        ).label("provider_turnover")
        provider_orders_expr = func.coalesce(
            func.sum(case((cascade_marker, 1), else_=0)), 0,
        ).label("provider_orders")
        # Cascade platform profit — same per-order margin expr, restricted to
        # the cascade-routed slice. A subset of the total ``profit`` column.
        provider_profit_expr = func.coalesce(
            func.sum(case((cascade_marker, self._system_profit_expr()), else_=0)), 0,
        ).label("provider_profit")

        stmt = (
            select(
                bucket,
                func.coalesce(func.sum(Order.amount_usdt), 0).label("turnover"),
                func.coalesce(func.sum(self._system_profit_expr()), 0).label("profit"),
                func.count(Order.id).label("orders"),
                provider_turnover_expr,
                provider_orders_expr,
                provider_profit_expr,
            )
            .where(Order.status == OrderStatus.SUCCESS, *dc)
            .group_by(bucket)
            .order_by(bucket.asc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            TimeseriesPoint(
                ts=row.bucket,
                turnover_usdt=float(row.turnover or 0),
                profit_usdt=float(row.profit or 0),
                orders=int(row.orders or 0),
                provider_turnover_usdt=float(row.provider_turnover or 0),
                provider_profit_usdt=float(row.provider_profit or 0),
                provider_orders=int(row.provider_orders or 0),
            )
            for row in rows
            if row.bucket is not None
        ]

    async def get_realtime_metrics(self) -> RealtimeMetrics:
        since_24h = utcnow() - timedelta(hours=24)

        merchants_online_24h: int = (await self.session.execute(
            select(func.count(func.distinct(Order.merchant_id))).where(
                Order.created_at >= since_24h
            )
        )).scalar_one()

        traders_online_24h: int = (await self.session.execute(
            select(func.count(func.distinct(Order.trader_id))).where(
                Order.trader_id.isnot(None),
                Order.created_at >= since_24h,
            )
        )).scalar_one()

        pending_withdrawals: int = (await self.session.execute(
            select(func.count(WithdrawalRequest.id)).where(
                WithdrawalRequest.status == WithdrawalStatus.PENDING
            )
        )).scalar_one()

        active_disputes: int = (await self.session.execute(
            select(func.count(Dispute.id)).where(
                Dispute.status.in_(ACTIVE_DISPUTE_STATUSES)
            )
        )).scalar_one()

        orders_active: int = (await self.session.execute(
            select(func.count(Order.id)).where(
                Order.status.in_(ACTIVE_ORDER_STATUSES)
            )
        )).scalar_one()

        payin_requests_24h: int = await self.count_payin_api_requests_since(since_24h)
        requests_rub_24h: float = await self.sum_payin_api_requests_rub_since(since_24h)

        return RealtimeMetrics(
            merchants_online_24h=merchants_online_24h,
            traders_online_24h=traders_online_24h,
            pending_withdrawals=pending_withdrawals,
            active_disputes=active_disputes,
            orders_active=orders_active,
            payin_requests_24h=payin_requests_24h,
            requests_rub_24h=requests_rub_24h,
        )

    async def get_new_orders_delta(self, cutoff: datetime) -> NewOrdersDelta:
        row = (await self.session.execute(
            select(
                func.count(Order.id),
                func.count(Order.id).filter(Order.status == OrderStatus.SUCCESS),
                func.count(Order.id).filter(Order.direction == PaymentDirection.PAYOUT),
                func.coalesce(
                    func.sum(Order.amount_usdt).filter(Order.status == OrderStatus.SUCCESS), 0
                ),
                func.coalesce(
                    func.sum(self._system_profit_expr()).filter(
                        Order.status == OrderStatus.SUCCESS
                    ),
                    0,
                ),
                func.coalesce(func.sum(Order.amount), 0),
            ).where(Order.created_at > cutoff)
        )).one()

        teamlead_rewards_usdt = await self._sum_teamlead_rewards_for_success(
            Order.created_at > cutoff
        )

        return NewOrdersDelta(
            total=row[0],
            success=row[1],
            payouts=row[2],
            turnover=float(row[3]),
            profit=float(row[4]) - teamlead_rewards_usdt,
            requests=float(row[5]),
        )

    async def get_updated_orders_delta(self, cutoff: datetime) -> UpdatedOrdersDelta:
        updated_conds = (
            Order.confirmed_at > cutoff,
            Order.created_at <= cutoff,
        )
        row = (await self.session.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.amount_usdt), 0),
                func.coalesce(func.sum(self._system_profit_expr()), 0),
            ).where(Order.status == OrderStatus.SUCCESS, *updated_conds)
        )).one()

        teamlead_rewards_usdt = await self._sum_teamlead_rewards_for_success(
            *updated_conds
        )

        return UpdatedOrdersDelta(
            new_successes=row[0],
            turnover=float(row[1]),
            profit=float(row[2]) - teamlead_rewards_usdt,
        )

    async def get_latest_snapshot(self) -> Optional[StatsSnapshot]:
        return (await self.session.execute(
            select(StatsSnapshot).order_by(StatsSnapshot.id.desc()).limit(1)
        )).scalar_one_or_none()

    async def upsert_snapshot(self, values: Dict) -> None:
        snapshot = await self.get_latest_snapshot()
        if snapshot:
            for k, v in values.items():
                setattr(snapshot, k, v)
        else:
            self.session.add(StatsSnapshot(**values))
        await self.session.flush()

    # ── Merchant-specific queries ──────────────────────────────

    async def get_merchant_order_aggregates(
        self,
        merchant_id: int,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> "MerchantOrderAggregates":
        mc = [Order.merchant_id == merchant_id]
        dc = self._date_conditions(date_from, date_to)
        conds = mc + dc

        turnover_usdt = float(
            (await self.session.execute(
                select(func.coalesce(func.sum(Order.amount_usdt), 0)).where(
                    Order.status == OrderStatus.SUCCESS, *conds
                )
            )).scalar_one()
        )
        fee_usdt = float(
            (await self.session.execute(
                select(func.coalesce(func.sum(Order.fee_usdt), 0)).where(
                    Order.status == OrderStatus.SUCCESS, *conds
                )
            )).scalar_one()
        )
        orders_total: int = (await self.session.execute(
            select(func.count(Order.id)).where(*conds)
        )).scalar_one()
        orders_success: int = (await self.session.execute(
            select(func.count(Order.id)).where(
                Order.status == OrderStatus.SUCCESS, *conds
            )
        )).scalar_one()
        orders_active: int = (await self.session.execute(
            select(func.count(Order.id)).where(
                Order.status.in_(ACTIVE_ORDER_STATUSES), *conds
            )
        )).scalar_one()
        orders_failed: int = (await self.session.execute(
            select(func.count(Order.id)).where(
                Order.status.in_([OrderStatus.FAILED, OrderStatus.CANCELED]), *conds
            )
        )).scalar_one()

        return MerchantOrderAggregates(
            turnover_usdt=turnover_usdt,
            fee_usdt=fee_usdt,
            orders_total=orders_total,
            orders_success=orders_success,
            orders_active=orders_active,
            orders_failed=orders_failed,
        )

    async def get_merchant_realtime_metrics(self, merchant_id: int) -> "MerchantRealtimeMetrics":
        # Migration 016 stores the terminal reference in `merchant_id`;
        # `user_id` is the merchant owner (users.id). Filtering by the new
        # column keeps per-terminal realtime stats scoped correctly.
        pending_withdrawals: int = (await self.session.execute(
            select(func.count(WithdrawalRequest.id)).where(
                WithdrawalRequest.status == WithdrawalStatus.PENDING,
                WithdrawalRequest.user_role == UserRole.MERCHANT,
                WithdrawalRequest.merchant_id == merchant_id,
            )
        )).scalar_one()

        active_disputes: int = (await self.session.execute(
            select(func.count(Dispute.id)).where(
                Dispute.merchant_id == merchant_id,
                Dispute.status.in_(ACTIVE_DISPUTE_STATUSES),
            )
        )).scalar_one()

        orders_active: int = (await self.session.execute(
            select(func.count(Order.id)).where(
                Order.merchant_id == merchant_id,
                Order.status.in_(ACTIVE_ORDER_STATUSES),
            )
        )).scalar_one()

        return MerchantRealtimeMetrics(
            pending_withdrawals=pending_withdrawals,
            active_disputes=active_disputes,
            orders_active=orders_active,
        )

    async def get_merchant_new_orders_delta(
        self, merchant_id: int, cutoff: datetime
    ) -> "MerchantNewOrdersDelta":
        conds = [Order.merchant_id == merchant_id, Order.created_at > cutoff]
        row = (await self.session.execute(
            select(
                func.count(Order.id),
                func.count(Order.id).filter(Order.status == OrderStatus.SUCCESS),
                func.count(Order.id).filter(Order.status.in_([OrderStatus.FAILED, OrderStatus.CANCELED])),
                func.coalesce(
                    func.sum(Order.amount_usdt).filter(Order.status == OrderStatus.SUCCESS), 0
                ),
                func.coalesce(
                    func.sum(Order.fee_usdt).filter(Order.status == OrderStatus.SUCCESS), 0
                ),
            ).where(*conds)
        )).one()

        return MerchantNewOrdersDelta(
            total=row[0], success=row[1], failed=row[2],
            turnover=float(row[3]), fee=float(row[4]),
        )

    async def get_merchant_updated_orders_delta(
        self, merchant_id: int, cutoff: datetime
    ) -> "MerchantUpdatedOrdersDelta":
        row = (await self.session.execute(
            select(
                func.count(Order.id),
                func.coalesce(func.sum(Order.amount_usdt), 0),
                func.coalesce(func.sum(Order.fee_usdt), 0),
            ).where(
                Order.merchant_id == merchant_id,
                Order.status == OrderStatus.SUCCESS,
                Order.confirmed_at > cutoff,
                Order.created_at <= cutoff,
            )
        )).one()

        return MerchantUpdatedOrdersDelta(
            new_successes=row[0], turnover=float(row[1]), fee=float(row[2]),
        )

    async def get_merchant_snapshot(self, merchant_id: int) -> Optional[MerchantStatsSnapshot]:
        return (await self.session.execute(
            select(MerchantStatsSnapshot).where(
                MerchantStatsSnapshot.merchant_id == merchant_id
            ).limit(1)
        )).scalar_one_or_none()

    async def upsert_merchant_snapshot(self, merchant_id: int, values: Dict) -> None:
        snapshot = await self.get_merchant_snapshot(merchant_id)
        if snapshot:
            for k, v in values.items():
                setattr(snapshot, k, v)
        else:
            self.session.add(MerchantStatsSnapshot(merchant_id=merchant_id, **values))
        await self.session.flush()

    async def get_failed_api_requests_rub(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> float:
        """Sum amounts from payin API requests that never created an order."""
        return await ch_logs.failed_payin_amount(date_from=date_from, date_to=date_to)

    async def count_payin_api_requests(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        merchant_id: Optional[int] = None,
    ) -> int:
        """Count all payin create API requests (successful + failed) in range.

        ``merchant_id`` scopes to a single merchant's API logs — used by the
        per-merchant «Выдача %» denominator. Без него — глобальный счётчик.
        """
        return await ch_logs.count_payin_requests(
            date_from=date_from, date_to=date_to, merchant_id=merchant_id,
        )

    async def count_payin_api_requests_since(
        self, cutoff: datetime, merchant_id: Optional[int] = None
    ) -> int:
        return await ch_logs.count_payin_requests(since=cutoff, merchant_id=merchant_id)

    async def sum_payin_api_requests_rub(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        merchant_id: Optional[int] = None,
    ) -> float:
        """Sum of `amount` across all payin create API requests in range.

        Source of truth for «Запросы руб»: matches what the 24h volume
        distribution table aggregates, so card totals and per-bucket sums
        reconcile exactly. ``merchant_id`` scopes to one merchant.
        """
        return await ch_logs.sum_payin_amount(
            date_from=date_from, date_to=date_to, merchant_id=merchant_id,
        )

    async def sum_payin_api_requests_rub_since(
        self, cutoff: datetime, merchant_id: Optional[int] = None
    ) -> float:
        return await ch_logs.sum_payin_amount(since=cutoff, merchant_id=merchant_id)

    async def get_failed_api_requests_rub_since(self, cutoff: datetime) -> float:
        """Sum amounts from payin API requests that never created an order, since cutoff."""
        return await ch_logs.failed_payin_amount(since=cutoff)

    async def list_order_creation_requests(
        self,
        skip: int = 0,
        limit: int = 50,
    ) -> tuple[list, int]:
        from types import SimpleNamespace
        from app.modules.merchants.models import Merchant
        from app.modules.users.models import User

        rows, total = await ch_logs.list_order_creation_requests(skip=skip, limit=limit)

        merchant_ids = {r["merchant_id"] for r in rows if r.get("merchant_id")}
        logins: dict[int, str] = {}
        if merchant_ids:
            res = await self.session.execute(
                select(Merchant.id, User.username)
                .join(User, Merchant.user_id == User.id)
                .where(Merchant.id.in_(merchant_ids))
            )
            logins = {mid: uname for mid, uname in res.all()}

        items = [
            SimpleNamespace(
                id=r["request_id"],
                merchant_id=r["merchant_id"],
                merchant_login=logins.get(r["merchant_id"]) if r.get("merchant_id") else None,
                request_data=r["request_data"],
                result=r["result"],
                response_time_ms=r["response_time_ms"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
        return items, total

    async def get_volume_distribution_24h(self) -> list[dict]:
        return await ch_logs.volume_distribution_24h()

    # ── «Мерчанты» tab: per-merchant funnel over a period ──────────────

    async def get_all_merchants_order_aggregates(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> List:
        """ONE grouped scan of PAYIN orders → per merchant: created count,
        success count, and the 13 RUB check-size bucket sums (successful orders).

        Single ``GROUP BY merchant_id`` with conditional ``case`` sums — no
        per-merchant fan-out — so the whole table is one indexed aggregate.
        Bucket edges come from :func:`merchant_metrics.check_size_bounds` (shared
        source of truth with the response assembly)."""
        from app.common.enums.finances import Currency
        from app.modules.stats.merchant_metrics import check_size_bounds

        dc = self._date_conditions(date_from, date_to)
        is_success = Order.status == OrderStatus.SUCCESS
        is_rub = Order.currency == Currency.RUB

        bucket_cols = []
        for i, (lo, hi) in enumerate(check_size_bounds()):
            cond = is_success & is_rub & (Order.amount >= lo)
            if hi is not None:
                cond = cond & (Order.amount < hi)
            bucket_cols.append(
                func.coalesce(func.sum(case((cond, Order.amount), else_=0)), 0).label(f"b{i}")
            )

        stmt = (
            select(
                Order.merchant_id.label("merchant_id"),
                func.count(Order.id).label("created"),
                func.coalesce(func.sum(case((is_success, 1), else_=0)), 0).label("success"),
                *bucket_cols,
            )
            .where(Order.direction == PaymentDirection.PAYIN, *dc)
            .group_by(Order.merchant_id)
        )
        return list((await self.session.execute(stmt)).all())

    async def get_merchant_logins(self, merchant_ids) -> Dict[int, str]:
        """``{merchant_id: username}`` for the given ids (one join query)."""
        ids = [m for m in merchant_ids if m]
        if not ids:
            return {}
        from app.modules.users.models import User

        res = await self.session.execute(
            select(Merchant.id, User.username)
            .join(User, Merchant.user_id == User.id)
            .where(Merchant.id.in_(ids))
        )
        return {mid: uname for mid, uname in res.all()}

    async def get_merchant_order_timeseries(
        self,
        merchant_id: int,
        date_from: Optional[datetime],
        date_to: Optional[datetime],
        granularity: str,
    ) -> List[dict]:
        """Per-bucket created/success counts for one merchant's PAYIN orders,
        bucketed by ``date_trunc(granularity, created_at)``. Mirrors
        :meth:`get_admin_timeseries`'s bucketing."""
        if granularity not in {"3hour", "hour", "day", "week", "month"}:
            raise ValueError(f"unsupported granularity: {granularity}")

        if granularity == "3hour":
            bucket = func.date_bin(
                text("interval '3 hours'"),
                Order.created_at,
                text("timestamptz '1970-01-01 00:00:00+00'"),
            ).label("bucket")
        else:
            bucket = func.date_trunc(granularity, Order.created_at).label("bucket")

        dc = self._date_conditions(date_from, date_to)
        is_success = Order.status == OrderStatus.SUCCESS
        stmt = (
            select(
                bucket,
                func.count(Order.id).label("created"),
                func.coalesce(func.sum(case((is_success, 1), else_=0)), 0).label("success"),
            )
            .where(
                Order.merchant_id == merchant_id,
                Order.direction == PaymentDirection.PAYIN,
                *dc,
            )
            .group_by(bucket)
            .order_by(bucket.asc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            {"ts": r.bucket, "created": int(r.created), "success": int(r.success)}
            for r in rows
        ]

    async def count_payin_requests_by_merchant(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> Dict[int, int]:
        """``{merchant_id: request_count}`` incl. non-created attempts (ClickHouse
        ``merchant_api_logs``). Fail-safe ``{}`` when CH is off/errors."""
        return await ch_logs.count_payin_requests_by_merchant(
            date_from=date_from, date_to=date_to
        )

    async def payin_requests_timeseries_by_merchant(
        self,
        merchant_id: int,
        date_from: Optional[datetime],
        date_to: Optional[datetime],
        granularity: str,
    ) -> List[dict]:
        """Per-bucket request counts for one merchant (ClickHouse). Each dict:
        ``bucket`` (unix seconds), ``requests``. Fail-safe ``[]``."""
        return await ch_logs.payin_requests_timeseries_by_merchant(
            merchant_id=merchant_id,
            date_from=date_from,
            date_to=date_to,
            granularity=granularity,
        )

    async def list_ready_requisites_for_snapshot(self) -> List[tuple]:
        """Requisites «в работе» (ready to accept payins) right now — the static
        pooling pre-filter, with no order/amount/balance checks. Column-only
        select (no ORM hydration) so the per-minute activity snapshot stays cheap.

        Returns tuples ``(requisite_id, trader_id, currency, limit_min_transaction,
        limit_max_transaction, limit_daily)``. INNER joins guarantee a limit row
        and an owning trader; matches ``PoolingService._get_base_query``'s static
        filters."""
        stmt = (
            select(
                Requisite.id,
                Requisite.trader_id,
                Requisite.currency,
                RequisiteLimit.limit_min_transaction,
                RequisiteLimit.limit_max_transaction,
                RequisiteLimit.limit_daily,
            )
            .join(RequisiteLimit, RequisiteLimit.requisite_id == Requisite.id)
            .join(Trader, Trader.user_id == Requisite.trader_id)
            .where(
                Requisite.is_active.is_(True),
                Requisite.is_archived.is_(False),
                Requisite.status == RequisiteStatus.ENABLED,
                Requisite.source == RequisiteSource.LOCAL,
                Trader.status == TraderStatus.ENABLED,
                Trader.is_payin_active.is_(True),
            )
        )
        return list((await self.session.execute(stmt)).all())

    async def get_all_merchant_ids(self) -> List[int]:
        result = await self.session.execute(select(Merchant.id))
        return [row[0] for row in result.all()]

    # ── Sidebar counters ───────────────────────────────────────

    async def count_active_orders_global(self) -> int:
        result = await self.session.execute(
            select(func.count(Order.id)).where(
                Order.status.in_(ACTIVE_ORDER_STATUSES)
            )
        )
        return int(result.scalar_one())

    async def count_active_disputes_global(self) -> int:
        result = await self.session.execute(
            select(func.count(Dispute.id)).where(
                Dispute.status.in_(ACTIVE_DISPUTE_STATUSES)
            )
        )
        return int(result.scalar_one())

    async def count_pending_withdrawals_global(self) -> int:
        result = await self.session.execute(
            select(func.count(WithdrawalRequest.id)).where(
                WithdrawalRequest.status == WithdrawalStatus.PENDING
            )
        )
        return int(result.scalar_one())

    async def count_active_orders_for_trader(self, trader_user_id: int) -> int:
        result = await self.session.execute(
            select(func.count(Order.id)).where(
                Order.trader_id == trader_user_id,
                Order.status.in_(ACTIVE_ORDER_STATUSES),
            )
        )
        return int(result.scalar_one())

    async def count_traffic_accepting_requisites_for_trader(
        self, trader_user_id: int
    ) -> int:
        """How many of the trader's own requisites can take a payin RIGHT NOW.

        Mirrors the amount-independent pooling gates via the shared
        ``ready_requisite_ids_select`` predicate — static state (is_active /
        not archived / ENABLED / LOCAL / trader ENABLED + is_payin_active /
        positive WORK-USDT balance) AND capacity (free concurrency slot + room
        for at least the minimum transaction in the daily/monthly limit). This
        is exactly "can receive a deal in the moment", not a coarse count.
        """
        from app.modules.requisites.repository import ready_requisite_ids_select

        sub = ready_requisite_ids_select(trader_user_id).subquery()
        result = await self.session.execute(select(func.count()).select_from(sub))
        return int(result.scalar_one())

    async def count_traffic_accepting_requisites_global(self) -> int:
        """Platform-wide count of requisites that can take a payin RIGHT NOW —
        same ``ready_requisite_ids_select`` predicate as the trader badge, with
        no trader scope. (Previously the trader and global counts diverged; they
        now share one source of truth.)"""
        from app.modules.requisites.repository import ready_requisite_ids_select

        sub = ready_requisite_ids_select(None).subquery()
        result = await self.session.execute(select(func.count()).select_from(sub))
        return int(result.scalar_one())

    async def count_active_disputes_for_trader(self, trader_user_id: int) -> int:
        result = await self.session.execute(
            select(func.count(Dispute.id))
            .select_from(Dispute)
            .join(Order, Order.id == Dispute.order_id)
            .where(
                Order.trader_id == trader_user_id,
                Dispute.status.in_(ACTIVE_DISPUTE_STATUSES),
            )
        )
        return int(result.scalar_one())

    async def count_active_orders_for_merchant_user(self, user_id: int) -> int:
        merchant_ids_sub = select(Merchant.id).where(Merchant.user_id == user_id)
        result = await self.session.execute(
            select(func.count(Order.id)).where(
                Order.merchant_id.in_(merchant_ids_sub),
                Order.status.in_(ACTIVE_ORDER_STATUSES),
            )
        )
        return int(result.scalar_one())

    async def count_active_disputes_for_merchant_user(self, user_id: int) -> int:
        merchant_ids_sub = select(Merchant.id).where(Merchant.user_id == user_id)
        result = await self.session.execute(
            select(func.count(Dispute.id)).where(
                Dispute.merchant_id.in_(merchant_ids_sub),
                Dispute.status.in_(ACTIVE_DISPUTE_STATUSES),
            )
        )
        return int(result.scalar_one())

    async def count_pending_withdrawals_for_merchant_user(self, user_id: int) -> int:

        merchant_ids_sub = select(Merchant.id).where(Merchant.user_id == user_id)
        legacy_branch = (
            WithdrawalRequest.merchant_id.is_(None)
            & WithdrawalRequest.user_id.in_(merchant_ids_sub)
        )
        result = await self.session.execute(
            select(func.count(WithdrawalRequest.id)).where(
                WithdrawalRequest.status == WithdrawalStatus.PENDING,
                WithdrawalRequest.user_role == UserRole.MERCHANT,
                or_(
                    WithdrawalRequest.merchant_id.in_(merchant_ids_sub),
                    legacy_branch,
                ),
            )
        )
        return int(result.scalar_one())

    async def count_pending_withdrawals_for_user(
        self, user_id: int, role: UserRole
    ) -> int:
        """Pending withdrawal count for trader/teamlead sidebar badges.

        Both roles submit withdrawals via the same endpoint, identified by
        (`user_id`, `user_role`). We rely on `user_role` to disambiguate
        because traders and teamleads may share underlying user records in
        edge cases.
        """
        result = await self.session.execute(
            select(func.count(WithdrawalRequest.id)).where(
                WithdrawalRequest.status == WithdrawalStatus.PENDING,
                WithdrawalRequest.user_id == user_id,
                WithdrawalRequest.user_role == role,
            )
        )
        return int(result.scalar_one())


@dataclass
class MerchantOrderAggregates:
    turnover_usdt: float
    fee_usdt: float
    orders_total: int
    orders_success: int
    orders_active: int
    orders_failed: int


@dataclass
class MerchantRealtimeMetrics:
    pending_withdrawals: int
    active_disputes: int
    orders_active: int


@dataclass
class MerchantNewOrdersDelta:
    total: int
    success: int
    failed: int
    turnover: float
    fee: float


@dataclass
class MerchantUpdatedOrdersDelta:
    new_successes: int
    turnover: float
    fee: float

# ── Receipt-check («Чекер») stats ────────────────────────────────────────────
# Read-only aggregations over receipt_checks for the admin «Чекер» stats tab.
# PostgreSQL-only (COUNT/SUM FILTER + date_trunc/date_bin); off any hot path.
# created_at is tz-aware, so all bucketing/filtering is forced to UTC.

_CHECKER_ALLOWED_GRANULARITY = {"3hour", "hour", "day", "week", "month"}


@dataclass
class ProviderRollupRow:
    provider_id: Optional[int]
    name: Optional[str]
    adapter_type: Optional[str]
    total: int
    success: int
    failed: int
    manual: int
    auto: int
    clean: int
    suspicious: int
    decided: int
    charged_usdt: float
    refunded_usdt: float
    charged_count: int


@dataclass
class TraderRollupRow:
    trader_user_id: int
    username: Optional[str]
    checks: int
    charged_usdt: float
    suspicious: int
    decided: int


@dataclass
class CheckerBucketRow:
    ts: datetime
    checks: int
    spent_usdt: float


def _checker_aware_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """The date bounds arrive naive-UTC; the column is tz-aware, so compare
    against aware-UTC to keep the window unambiguous."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _checker_window(date_from: Optional[datetime], date_to: Optional[datetime]) -> list:
    conds = []
    df, dt = _checker_aware_utc(date_from), _checker_aware_utc(date_to)
    if df is not None:
        conds.append(ReceiptCheck.created_at >= df)
    if dt is not None:
        conds.append(ReceiptCheck.created_at < dt)
    return conds


# Reused FILTER building blocks
def _checker_count_status(status: ReceiptCheckStatus):
    return func.count().filter(ReceiptCheck.status == status)


def _checker_charged_sum():
    return func.coalesce(
        func.sum(ReceiptCheck.price_usdt).filter(ReceiptCheck.charged.is_(True)), 0
    )


def _checker_refunded_sum():
    return func.coalesce(
        func.sum(ReceiptCheck.price_usdt).filter(ReceiptCheck.refunded.is_(True)), 0
    )


class ReceiptCheckStatsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def provider_rollup(
        self, date_from: Optional[datetime], date_to: Optional[datetime]
    ) -> List[ProviderRollupRow]:
        stmt = (
            select(
                ReceiptCheck.provider_id.label("provider_id"),
                ReceiptCheckProvider.name.label("name"),
                ReceiptCheckProvider.adapter_type.label("adapter_type"),
                func.count().label("total"),
                _checker_count_status(ReceiptCheckStatus.SUCCESS).label("success"),
                _checker_count_status(ReceiptCheckStatus.FAILED).label("failed"),
                func.count().filter(ReceiptCheck.trigger == ReceiptCheckTrigger.MANUAL.value).label("manual"),
                func.count().filter(ReceiptCheck.trigger == ReceiptCheckTrigger.AUTO.value).label("auto"),
                func.count().filter(ReceiptCheck.is_clean.is_(True)).label("clean"),
                func.count().filter(ReceiptCheck.is_clean.is_(False)).label("suspicious"),
                func.count().filter(ReceiptCheck.is_clean.isnot(None)).label("decided"),
                _checker_charged_sum().label("charged_usdt"),
                _checker_refunded_sum().label("refunded_usdt"),
                func.count().filter(ReceiptCheck.charged.is_(True)).label("charged_count"),
            )
            .select_from(ReceiptCheck)
            .outerjoin(ReceiptCheckProvider, ReceiptCheckProvider.id == ReceiptCheck.provider_id)
            .where(*_checker_window(date_from, date_to))
            .group_by(ReceiptCheck.provider_id, ReceiptCheckProvider.name, ReceiptCheckProvider.adapter_type)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            ProviderRollupRow(
                provider_id=r.provider_id, name=r.name, adapter_type=r.adapter_type,
                total=r.total, success=r.success, failed=r.failed,
                manual=r.manual, auto=r.auto, clean=r.clean, suspicious=r.suspicious,
                decided=r.decided, charged_usdt=float(r.charged_usdt),
                refunded_usdt=float(r.refunded_usdt), charged_count=r.charged_count,
            )
            for r in rows
        ]

    async def top_traders(
        self, date_from: Optional[datetime], date_to: Optional[datetime], limit: int = 20
    ) -> List[TraderRollupRow]:
        from app.modules.users.models import User

        total = func.count().label("checks")
        stmt = (
            select(
                ReceiptCheck.trader_user_id.label("trader_user_id"),
                User.username.label("username"),
                total,
                _checker_charged_sum().label("charged_usdt"),
                func.count().filter(ReceiptCheck.is_clean.is_(False)).label("suspicious"),
                func.count().filter(ReceiptCheck.is_clean.isnot(None)).label("decided"),
            )
            .select_from(ReceiptCheck)
            .join(User, User.id == ReceiptCheck.trader_user_id)
            .where(ReceiptCheck.trader_user_id.isnot(None), *_checker_window(date_from, date_to))
            .group_by(ReceiptCheck.trader_user_id, User.username)
            .order_by(total.desc())
            .limit(limit)
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            TraderRollupRow(
                trader_user_id=r.trader_user_id, username=r.username, checks=r.checks,
                charged_usdt=float(r.charged_usdt), suspicious=r.suspicious, decided=r.decided,
            )
            for r in rows
        ]

    async def timeseries(
        self, date_from: Optional[datetime], date_to: Optional[datetime], granularity: str
    ) -> List[CheckerBucketRow]:
        if granularity not in _CHECKER_ALLOWED_GRANULARITY:
            raise ValueError(f"unsupported granularity: {granularity}")
        col = func.timezone("UTC", ReceiptCheck.created_at)  # tz-aware -> naive UTC
        if granularity == "3hour":
            bucket = func.date_bin(
                text("interval '3 hours'"), col,
                text("timestamp '1970-01-01 00:00:00'"),
            ).label("bucket")
        else:
            bucket = func.date_trunc(granularity, col).label("bucket")
        stmt = (
            select(
                bucket,
                func.count().label("checks"),
                _checker_charged_sum().label("spent_usdt"),
            )
            .select_from(ReceiptCheck)
            .where(*_checker_window(date_from, date_to))
            .group_by(bucket)
            .order_by(bucket.asc())
        )
        rows = (await self.session.execute(stmt)).all()
        return [
            CheckerBucketRow(ts=r.bucket, checks=r.checks, spent_usdt=float(r.spent_usdt))
            for r in rows
        ]
