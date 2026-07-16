from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import logging

from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.common.enums.users import UserRole
from app.common.types import utcnow
from app.infrastructure.cache.redis import cache_json
from app.modules.base.service import BaseService
from app.modules.stats import activity_ch
from app.modules.stats.repository import (
    ProviderRollupRow,
    ReceiptCheckStatsRepository,
    StatsRepository,
    TraderRollupRow,
)
from app.modules.users.models import User

_ADMIN_STATS_TTL = 30
_TIMESERIES_TTL = 60
_VOLUME_DIST_TTL = 60
_ACTIVITY_TTL = 60

# Receipt-check («Чекер») stats tab
_UNKNOWN_PROVIDER = "удалён/неизвестен"
_CHECKER_STATS_TTL = 30
_CHECKER_TIMESERIES_TTL = 60


def _stamp(dt: Optional[datetime]) -> str:
    """Stable cache-key fragment for an optional datetime."""
    return dt.isoformat() if dt else ""


def _resolve_range(
    date_from: Optional[datetime], date_to: Optional[datetime], *, default_days: int,
) -> tuple:
    """Normalise a query range to aware-UTC ``(df, dt)``; default to the last
    ``default_days`` when both ends are missing (same UX as the date pickers)."""
    def _aware(d: Optional[datetime]) -> Optional[datetime]:
        if d is None:
            return None
        return d.replace(tzinfo=timezone.utc) if d.tzinfo is None else d.astimezone(timezone.utc)

    df, dt = _aware(date_from), _aware(date_to)
    if df is None and dt is None:
        dt = utcnow()
        df = dt - timedelta(days=default_days)
    elif df is None:
        df = dt - timedelta(days=default_days)
    elif dt is None:
        dt = utcnow()
    return df, dt


def _auto_granularity(df: datetime, dt: datetime) -> str:
    """Bucket size auto-picked from the span (matches the dashboard chart rule)."""
    span = dt - df
    if span <= timedelta(hours=24):
        return "hour"
    if span <= timedelta(days=31):
        return "day"
    if span <= timedelta(days=180):
        return "week"
    return "month"


def _bucket_truncate(ts: datetime, gran: str) -> datetime:
    """Snap ``ts`` down to its ``gran`` bucket start (UTC). Matches Postgres
    ``date_trunc`` / ClickHouse ``toStartOf*`` and the backfill walker."""
    if gran == "hour":
        return ts.replace(minute=0, second=0, microsecond=0)
    if gran == "3hour":
        return ts.replace(hour=(ts.hour // 3) * 3, minute=0, second=0, microsecond=0)
    if gran == "day":
        return ts.replace(hour=0, minute=0, second=0, microsecond=0)
    if gran == "week":  # ISO Monday
        start = ts - timedelta(days=ts.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _bucket_advance(ts: datetime, gran: str) -> datetime:
    """Next bucket boundary after ``ts`` for ``gran``."""
    if gran == "hour":
        return ts + timedelta(hours=1)
    if gran == "3hour":
        return ts + timedelta(hours=3)
    if gran == "day":
        return ts + timedelta(days=1)
    if gran == "week":
        return ts + timedelta(weeks=1)
    if ts.month == 12:
        return ts.replace(year=ts.year + 1, month=1, day=1)
    return ts.replace(month=ts.month + 1, day=1)


def _merge_intervals(intervals: List[tuple]) -> List[tuple]:
    """Merge overlapping/touching ``[min, max]`` ranges into consolidated bars.
    e.g. ``(100,1000),(500,2000),(5000,5500)`` → ``[(100,2000),(5000,5500)]``."""
    ordered = sorted(
        (float(a), float(b)) for a, b in intervals if a is not None and b is not None
    )
    merged: List[list] = []
    for lo, hi in ordered:
        if hi < lo:
            lo, hi = hi, lo
        if merged and lo <= merged[-1][1]:
            if hi > merged[-1][1]:
                merged[-1][1] = hi
        else:
            merged.append([lo, hi])
    return [(lo, hi) for lo, hi in merged]


def _segments(bar_lo: float, bar_hi: float, intervals: List[tuple]) -> List[dict]:
    """Coverage step-function over ``[bar_lo, bar_hi]``: consecutive slices with a
    constant number of covering requisites (adjacent equal-count slices merged).
    Sweep-line over interval start/end deltas — O(k log k). Powers the hover
    tooltip's «N реквизитов на этом уровне».

    A fixed-amount requisite (``limit_min == limit_max``) is a zero-width interval
    covering a single amount (measure-zero); it is intentionally excluded from the
    positive-width coverage bands here — attributing it to the adjacent band would
    over-count that whole band, and a hover almost never lands on the exact point.
    Such a requisite is still reflected in the bar geometry and in ``trader_count``
    (the bar's colour)."""
    from collections import defaultdict

    delta: dict = defaultdict(int)
    for a, b in intervals:
        lo = max(float(a), bar_lo)
        hi = min(float(b), bar_hi)
        if hi <= lo:
            continue
        delta[lo] += 1
        delta[hi] -= 1
    positions = sorted(delta)
    out: List[dict] = []
    count = 0
    for i in range(len(positions) - 1):
        count += delta[positions[i]]
        lo, hi = positions[i], positions[i + 1]
        if count <= 0 or hi <= lo:
            continue
        if out and out[-1]["requisites"] == count and out[-1]["to_amount"] == lo:
            out[-1]["to_amount"] = hi
        else:
            out.append({"from_amount": lo, "to_amount": hi, "requisites": count})
    return out


@dataclass
class AdminStats:
    turnover_usdt: float
    profit_usdt: float
    requests_rub: float
    payout_count: int
    payout_pct: float
    payin_requests_total: int
    payin_requests_24h: int
    orders_total: int
    orders_success: int
    orders_active: int
    conversion_pct: float
    merchants_online_24h: int
    traders_online_24h: int
    pending_withdrawals: int
    active_disputes: int
    requests_rub_24h: float


@dataclass
class MerchantStats:
    turnover_usdt: float
    fee_usdt: float
    orders_total: int
    orders_success: int
    orders_active: int
    orders_failed: int
    conversion_pct: float
    pending_withdrawals: int
    active_disputes: int
    requests_rub: float = 0.0
    payin_requests_total: int = 0
    payout_pct: float = 0.0


@dataclass
class ActiveStats:
    orders_active: int
    active_disputes: int
    pending_withdrawals: int
    requisites_traffic_active: int = 0


class StatsService(BaseService):

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = StatsRepository(session)

    async def compute_full(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> AdminStats:
        agg = await self.repository.get_order_aggregates(date_from, date_to)
        rt = await self.repository.get_realtime_metrics()

        payin_requests_total = await self.repository.count_payin_api_requests(
            date_from, date_to
        )
        requests_rub_total = await self.repository.sum_payin_api_requests_rub(
            date_from, date_to
        )

        # Выдача % = доля созданных ордеров от общего числа запросов на сделку
        # от мерчантов (внешнее API + бот). Если запросов 0 — отдаём 0, чтобы
        # дашборд не падал на делении.
        payout_pct = round(
            agg.orders_total / payin_requests_total * 100
            if payin_requests_total
            else 0,
            2,
        )
        conversion_pct = round(
            agg.orders_success / agg.orders_total * 100 if agg.orders_total else 0, 2
        )

        return AdminStats(
            turnover_usdt=agg.turnover_usdt,
            profit_usdt=agg.profit_usdt,
            requests_rub=requests_rub_total,
            payout_count=agg.payout_count,
            payout_pct=payout_pct,
            payin_requests_total=payin_requests_total,
            payin_requests_24h=rt.payin_requests_24h,
            orders_total=agg.orders_total,
            orders_success=agg.orders_success,
            orders_active=agg.orders_active,
            conversion_pct=conversion_pct,
            merchants_online_24h=rt.merchants_online_24h,
            traders_online_24h=rt.traders_online_24h,
            pending_withdrawals=rt.pending_withdrawals,
            active_disputes=rt.active_disputes,
            requests_rub_24h=rt.requests_rub_24h,
        )

    async def refresh_snapshot(self) -> None:
        """Full recompute -> upsert single snapshot row. Called by Celery."""
        stats = await self.compute_full()
        cutoff = utcnow()

        await self.repository.upsert_snapshot(dict(
            turnover_usdt=stats.turnover_usdt,
            profit_usdt=stats.profit_usdt,
            requests_rub=stats.requests_rub,
            orders_total=stats.orders_total,
            orders_success=stats.orders_success,
            orders_active=stats.orders_active,
            payout_count=stats.payout_count,
            payout_pct=stats.payout_pct,
            payin_requests_total=stats.payin_requests_total,
            payin_requests_24h=stats.payin_requests_24h,
            conversion_pct=stats.conversion_pct,
            merchants_online_24h=stats.merchants_online_24h,
            traders_online_24h=stats.traders_online_24h,
            pending_withdrawals=stats.pending_withdrawals,
            active_disputes=stats.active_disputes,
            requests_rub_24h=stats.requests_rub_24h,
            data_cutoff_at=cutoff,
        ))

    async def get_cached_stats(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> AdminStats:
        """Read-through Redis cache over ``_compute_admin_stats``.

        Wraps the existing snapshot+delta logic with a short-TTL Redis
        layer so each admin dashboard refresh (5-10s polling) doesn't
        replay the heavy ``CAST(merchant_api_logs.request_body AS JSONB)``
        aggregates — those compete with order creation for the same
        Postgres page cache.
        """
        cache_key = f"stats:admin:{_stamp(date_from)}:{_stamp(date_to)}"
        payload = await cache_json(
            cache_key,
            _ADMIN_STATS_TTL,
            lambda: self._compute_admin_stats(date_from, date_to),
        )
        if isinstance(payload, dict):
            return AdminStats(**payload)
        return payload

    async def _compute_admin_stats(
        self,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> dict:
        """Compute admin stats and return as plain dict (for caching).

        Returns dict rather than ``AdminStats`` so the result survives a
        JSON round-trip through Redis. Callers reconstruct the dataclass.
        """
        if date_from or date_to:
            return asdict(await self.compute_full(date_from, date_to))

        try:
            snapshot = await self.repository.get_latest_snapshot()
        except ProgrammingError:
            logger.warning("stats_snapshots table missing, falling back to compute_full")
            await self.session.rollback()
            return asdict(await self.compute_full())

        if not snapshot:
            return asdict(await self.compute_full())

        cutoff = snapshot.data_cutoff_at

        new = await self.repository.get_new_orders_delta(cutoff)
        upd = await self.repository.get_updated_orders_delta(cutoff)
        # доливы that became COMPLETED after the snapshot — keeps долив profit
        # live on the cached dashboard, not lagging the 10-min snapshot refresh.
        doliv_margin_since = await self.repository.sum_doliv_margin_since(cutoff)
        rt = await self.repository.get_realtime_metrics()

        new_requests = await self.repository.count_payin_api_requests_since(cutoff)
        new_requests_rub = await self.repository.sum_payin_api_requests_rub_since(cutoff)

        total = snapshot.orders_total + new.total
        success = snapshot.orders_success + new.success + upd.new_successes
        payouts = snapshot.payout_count + new.payouts
        # Старые снапшоты могли быть записаны до миграции 019 — подхватываем 0.
        snapshot_requests = getattr(snapshot, "payin_requests_total", 0) or 0
        payin_requests_total = snapshot_requests + new_requests

        return asdict(AdminStats(
            turnover_usdt=float(snapshot.turnover_usdt) + new.turnover + upd.turnover,
            profit_usdt=float(snapshot.profit_usdt) + new.profit + upd.profit + doliv_margin_since,
            requests_rub=float(snapshot.requests_rub) + new_requests_rub,
            payout_count=payouts,
            # Выдача % = созданные ордера / запросы на сделку * 100.
            payout_pct=round(
                total / payin_requests_total * 100 if payin_requests_total else 0,
                2,
            ),
            payin_requests_total=payin_requests_total,
            payin_requests_24h=rt.payin_requests_24h,
            orders_total=total,
            orders_success=success,
            orders_active=rt.orders_active,
            conversion_pct=round(success / total * 100 if total else 0, 2),
            merchants_online_24h=rt.merchants_online_24h,
            traders_online_24h=rt.traders_online_24h,
            pending_withdrawals=rt.pending_withdrawals,
            active_disputes=rt.active_disputes,
            requests_rub_24h=rt.requests_rub_24h,
        ))

    # ── Merchant stats ─────────────────────────────────────────

    async def compute_merchant_full(
        self,
        merchant_id: int,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> MerchantStats:
        agg = await self.repository.get_merchant_order_aggregates(merchant_id, date_from, date_to)
        rt = await self.repository.get_merchant_realtime_metrics(merchant_id)

        payin_requests_total = await self.repository.count_payin_api_requests(
            date_from, date_to, merchant_id=merchant_id
        )
        requests_rub = await self.repository.sum_payin_api_requests_rub(
            date_from, date_to, merchant_id=merchant_id
        )

        conversion_pct = round(
            agg.orders_success / agg.orders_total * 100 if agg.orders_total else 0, 2
        )

        payout_pct = round(
            agg.orders_total / payin_requests_total * 100
            if payin_requests_total
            else 0,
            2,
        )

        return MerchantStats(
            turnover_usdt=agg.turnover_usdt,
            fee_usdt=agg.fee_usdt,
            orders_total=agg.orders_total,
            orders_success=agg.orders_success,
            orders_active=agg.orders_active,
            orders_failed=agg.orders_failed,
            conversion_pct=conversion_pct,
            pending_withdrawals=rt.pending_withdrawals,
            active_disputes=rt.active_disputes,
            requests_rub=requests_rub,
            payin_requests_total=payin_requests_total,
            payout_pct=payout_pct,
        )

    async def refresh_merchant_snapshots(self) -> None:
        """Recompute snapshots for all merchants. Called by Celery."""
        merchant_ids = await self.repository.get_all_merchant_ids()
        cutoff = utcnow()

        for mid in merchant_ids:
            stats = await self.compute_merchant_full(mid)
            await self.repository.upsert_merchant_snapshot(mid, dict(
                turnover_usdt=stats.turnover_usdt,
                fee_usdt=stats.fee_usdt,
                orders_total=stats.orders_total,
                orders_success=stats.orders_success,
                orders_active=stats.orders_active,
                orders_failed=stats.orders_failed,
                conversion_pct=stats.conversion_pct,
                pending_withdrawals=stats.pending_withdrawals,
                active_disputes=stats.active_disputes,
                requests_rub=stats.requests_rub,
                payin_requests_total=stats.payin_requests_total,
                data_cutoff_at=cutoff,
            ))

    async def get_cached_merchant_stats(
        self,
        merchant_id: int,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> MerchantStats:
        try:
            # Кэшируем ТОЛЬКО общий случай
            if not date_from and not date_to:
                payload = await cache_json(
                    f"stats:merchant:{merchant_id}",
                    _ADMIN_STATS_TTL,
                    lambda: self._get_cached_merchant_stats_inner(merchant_id),
                )
                if isinstance(payload, dict):
                    return MerchantStats(**payload)
                return payload
            return await self._get_cached_merchant_stats_inner(merchant_id, date_from, date_to)
        except ProgrammingError as exc:
            if "UndefinedTableError" in str(exc) or "does not exist" in str(exc):
                logger.warning("merchant_stats_snapshots table missing, returning zeros")
                return MerchantStats(
                    turnover_usdt=0, fee_usdt=0,
                    orders_total=0, orders_success=0, orders_active=0, orders_failed=0,
                    conversion_pct=0, pending_withdrawals=0, active_disputes=0,
                    requests_rub=0, payin_requests_total=0, payout_pct=0,
                )
            raise

    async def _get_cached_merchant_stats_inner(
        self,
        merchant_id: int,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> MerchantStats:
        if date_from or date_to:
            return await self.compute_merchant_full(merchant_id, date_from, date_to)

        snapshot = await self.repository.get_merchant_snapshot(merchant_id)
        if not snapshot:
            return await self.compute_merchant_full(merchant_id)

        cutoff = snapshot.data_cutoff_at

        new = await self.repository.get_merchant_new_orders_delta(merchant_id, cutoff)
        upd = await self.repository.get_merchant_updated_orders_delta(merchant_id, cutoff)
        rt = await self.repository.get_merchant_realtime_metrics(merchant_id)

        req_rub_delta = await self.repository.sum_payin_api_requests_rub_since(
            cutoff, merchant_id=merchant_id
        )
        payin_req_delta = await self.repository.count_payin_api_requests_since(
            cutoff, merchant_id=merchant_id
        )

        total = snapshot.orders_total + new.total
        success = snapshot.orders_success + new.success + upd.new_successes
        failed = snapshot.orders_failed + new.failed

        requests_rub = float(getattr(snapshot, "requests_rub", 0) or 0) + req_rub_delta
        payin_requests_total = int(getattr(snapshot, "payin_requests_total", 0) or 0) + payin_req_delta

        return MerchantStats(
            turnover_usdt=float(snapshot.turnover_usdt) + new.turnover + upd.turnover,
            fee_usdt=float(snapshot.fee_usdt) + new.fee + upd.fee,
            orders_total=total,
            orders_success=success,
            orders_active=rt.orders_active,
            orders_failed=failed,
            conversion_pct=round(success / total * 100 if total else 0, 2),
            pending_withdrawals=rt.pending_withdrawals,
            active_disputes=rt.active_disputes,
            requests_rub=requests_rub,
            payin_requests_total=payin_requests_total,
            payout_pct=round(total / payin_requests_total * 100 if payin_requests_total else 0, 2),
        )

    # ── Active counters ────────────────────────────────────────

    async def get_active_stats(self, user: User) -> ActiveStats:
        """Lightweight realtime counters of active entities for the given user.

        Returned numbers are scoped to what the user is allowed to see and
        are intended for badges/widgets that need cheap polling.
        """
        role = user.role
        requisites_traffic_active = 0
        if role == UserRole.ADMIN:
            orders_active = await self.repository.count_active_orders_global()
            active_disputes = await self.repository.count_active_disputes_global()
            pending_withdrawals = await self.repository.count_pending_withdrawals_global()
            requisites_traffic_active = (
                await self.repository.count_traffic_accepting_requisites_global()
            )
        elif role == UserRole.TRADER:
            orders_active = await self.repository.count_active_orders_for_trader(user.id)
            active_disputes = await self.repository.count_active_disputes_for_trader(user.id)
            pending_withdrawals = await self.repository.count_pending_withdrawals_for_user(
                user.id, UserRole.TRADER
            )
            requisites_traffic_active = (
                await self.repository.count_traffic_accepting_requisites_for_trader(
                    user.id
                )
            )
        elif role == UserRole.MERCHANT:
            orders_active = await self.repository.count_active_orders_for_merchant_user(user.id)
            active_disputes = await self.repository.count_active_disputes_for_merchant_user(user.id)
            pending_withdrawals = await self.repository.count_pending_withdrawals_for_merchant_user(user.id)
        elif role == UserRole.TEAMLEAD:
            orders_active = 0
            active_disputes = 0
            pending_withdrawals = await self.repository.count_pending_withdrawals_for_user(
                user.id, UserRole.TEAMLEAD
            )
        else:
            orders_active = 0
            active_disputes = 0
            pending_withdrawals = 0

        return ActiveStats(
            orders_active=orders_active,
            active_disputes=active_disputes,
            pending_withdrawals=pending_withdrawals,
            requisites_traffic_active=requisites_traffic_active,
        )

    async def list_order_creation_requests(
        self,
        skip: int = 0,
        limit: int = 50,
    ):
        rows, total = await self.repository.list_order_creation_requests(skip=skip, limit=limit)
        
        items = []
        for row in rows:
            req_data = row.request_data or {}
            res_data = row.result or {}
            
            amount = req_data.get("amount")
            # Safely extract amount from JSON to prevent errors on corrupted inputs
            if amount is not None:
                try:
                    amount = float(amount)
                except (ValueError, TypeError):
                    amount = None
                    
            items.append({
                "id": row.id,
                "merchant_id": row.merchant_id,
                "merchant_login": row.merchant_login,
                "amount_rub": amount,
                "method": req_data.get("method") or req_data.get("payment_method"),
                "success": bool(res_data.get("success", False)),
                "response_time_ms": row.response_time_ms,
                "created_at": row.created_at,
            })
            
        return {"items": items, "total": total}

    async def get_volume_distribution_24h(self) -> list[dict]:
        return await cache_json(
            "stats:admin:volume_distribution_24h",
            _VOLUME_DIST_TTL,
            self.repository.get_volume_distribution_24h,
        )

    async def get_admin_timeseries(
        self,
        date_from: Optional[datetime],
        date_to: Optional[datetime],
        granularity: Optional[str] = None,
    ) -> dict:
        """Compute the admin dashboard turnover / profit chart series.

        Wrapped in a 60s Redis cache so repeated chart refreshes don't
        replay the bucketed aggregate. The cache key includes the input
        params verbatim — different ranges/granularities cache separately.

        If ``granularity`` is omitted, picks a sensible bucket based on the
        date range:
          • range ≤ 24h        → ``hour``
          • range ≤ 31d        → ``day``
          • range ≤ 180d       → ``week``
          • otherwise          → ``month``
        Defaults the range to "last 7 days" when both ``date_from`` and
        ``date_to`` are missing — same UX as the dashboard date pickers.
        """
        cache_key = (
            f"stats:admin:timeseries:{_stamp(date_from)}:{_stamp(date_to)}:"
            f"{granularity or 'auto'}"
        )
        return await cache_json(
            cache_key,
            _TIMESERIES_TTL,
            lambda: self._compute_admin_timeseries(date_from, date_to, granularity),
        )

    async def _compute_admin_timeseries(
        self,
        date_from: Optional[datetime],
        date_to: Optional[datetime],
        granularity: Optional[str] = None,
    ) -> dict:
        # Query params arrive as naive UTC (see ``UnixTimestamp``), while
        # ``utcnow()`` returns tz-aware. Mixing the two in arithmetic raises
        # TypeError, so normalise both to aware-UTC up front and only strip
        # tz when handing values to the repo (it expects naive).
        def _ensure_aware(d: Optional[datetime]) -> Optional[datetime]:
            if d is None:
                return None
            if d.tzinfo is None:
                return d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc)

        df = _ensure_aware(date_from)
        dt = _ensure_aware(date_to)
        if df is None and dt is None:
            dt = utcnow()
            df = dt - timedelta(days=7)
        elif df is None:
            assert dt is not None
            df = dt - timedelta(days=7)
        elif dt is None:
            dt = utcnow()

        if granularity is None:
            span = dt - df
            if span <= timedelta(hours=24):
                granularity = "hour"
            elif span <= timedelta(days=31):
                granularity = "day"
            elif span <= timedelta(days=180):
                granularity = "week"
            else:
                granularity = "month"

        points = await self.repository.get_admin_timeseries(df, dt, granularity)

        # Backfill empty buckets with zeros so the chart shows a continuous
        # series instead of jumping over days/hours without orders. Walk the
        # range from ``df`` (truncated to bucket boundary) up to ``dt`` in
        # ``granularity`` steps and merge with what the repo returned.
        def _truncate(ts: datetime, gran: str) -> datetime:
            if gran == "hour":
                return ts.replace(minute=0, second=0, microsecond=0)
            if gran == "3hour":
                return ts.replace(hour=(ts.hour // 3) * 3, minute=0, second=0, microsecond=0)
            if gran == "day":
                return ts.replace(hour=0, minute=0, second=0, microsecond=0)
            if gran == "week":
                # ISO Monday — matches Postgres ``date_trunc('week', ...)``.
                start = ts - timedelta(days=ts.weekday())
                return start.replace(hour=0, minute=0, second=0, microsecond=0)
            # month
            return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        def _advance(ts: datetime, gran: str) -> datetime:
            if gran == "hour":
                return ts + timedelta(hours=1)
            if gran == "3hour":
                return ts + timedelta(hours=3)
            if gran == "day":
                return ts + timedelta(days=1)
            if gran == "week":
                return ts + timedelta(weeks=1)
            # month
            if ts.month == 12:
                return ts.replace(year=ts.year + 1, month=1, day=1)
            return ts.replace(month=ts.month + 1, day=1)

        # Repo bucket timestamps come from Postgres ``date_trunc`` and are
        # naive UTC; normalise the same way our walker generates them so the
        # equality lookup in ``by_ts`` works.
        def _normalise_repo_ts(d: datetime) -> datetime:
            if d.tzinfo is None:
                return d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc)

        by_ts: dict[datetime, "TimeseriesPoint"] = {  # type: ignore[name-defined]
            _normalise_repo_ts(p.ts): p for p in points
        }

        cursor = _truncate(df, granularity)
        end = _truncate(dt, granularity)
        # Hard cap to guard against pathologically long ranges.
        max_buckets = 2000
        out_points: list[dict] = []
        while cursor <= end and len(out_points) < max_buckets:
            existing = by_ts.get(cursor)
            ts_unix = int(cursor.timestamp())
            if existing is not None:
                out_points.append({
                    "ts": ts_unix,
                    "turnover_usdt": existing.turnover_usdt,
                    "profit_usdt": existing.profit_usdt,
                    "orders": existing.orders,
                    "provider_turnover_usdt": existing.provider_turnover_usdt,
                    "provider_profit_usdt": existing.provider_profit_usdt,
                    "provider_orders": existing.provider_orders,
                })
            else:
                out_points.append({
                    "ts": ts_unix,
                    "turnover_usdt": 0.0,
                    "profit_usdt": 0.0,
                    "orders": 0,
                    "provider_turnover_usdt": 0.0,
                    "provider_profit_usdt": 0.0,
                    "provider_orders": 0,
                })
            cursor = _advance(cursor, granularity)

        return {
            "granularity": granularity,
            "points": out_points,
        }

    # ── «Мерчанты» tab ──────────────────────────────────────────────────────

    async def get_merchant_stats(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> list:
        """Per-merchant funnel table over a period, Redis-cached 60s (key per
        range). Defaults the range to the last 7 days."""
        cache_key = f"stats:admin:merchants:{_stamp(date_from)}:{_stamp(date_to)}"
        return await cache_json(
            cache_key,
            _TIMESERIES_TTL,
            lambda: self._compute_merchant_stats(date_from, date_to),
        )

    async def _compute_merchant_stats(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
    ) -> list:
        from app.modules.stats.merchant_metrics import (
            build_merchant_row,
            check_size_bounds,
        )

        df, dt = _resolve_range(date_from, date_to, default_days=7)
        n_buckets = len(check_size_bounds())

        agg_rows = await self.repository.get_all_merchants_order_aggregates(df, dt)
        requests = await self.repository.count_payin_requests_by_merchant(df, dt)

        agg_by_id = {r.merchant_id: r for r in agg_rows if r.merchant_id is not None}
        ids = set(agg_by_id) | set(requests)
        logins = await self.repository.get_merchant_logins(ids)

        rows: list = []
        for mid in ids:
            r = agg_by_id.get(mid)
            buckets = (
                [getattr(r, f"b{i}") for i in range(n_buckets)]
                if r is not None else [0] * n_buckets
            )
            rows.append(build_merchant_row(
                merchant_id=mid,
                merchant_login=logins.get(mid),
                requests=requests.get(mid, 0),
                orders_created=r.created if r is not None else 0,
                orders_success=r.success if r is not None else 0,
                buckets=buckets,
            ))
        # Busiest merchants first (requests, then created, then success).
        rows.sort(
            key=lambda x: (x["requests"], x["orders_created"], x["orders_success"]),
            reverse=True,
        )
        return rows

    async def get_merchant_timeseries(
        self,
        merchant_id: int,
        date_from: Optional[datetime],
        date_to: Optional[datetime],
        granularity: Optional[str] = None,
    ) -> dict:
        """Per-merchant requests/created/success time-series for the chart,
        Redis-cached 60s (key per merchant+range+granularity)."""
        cache_key = (
            f"stats:admin:merchant_ts:{int(merchant_id)}:{_stamp(date_from)}:"
            f"{_stamp(date_to)}:{granularity or 'auto'}"
        )
        return await cache_json(
            cache_key,
            _TIMESERIES_TTL,
            lambda: self._compute_merchant_timeseries(
                merchant_id, date_from, date_to, granularity
            ),
        )

    async def _compute_merchant_timeseries(
        self,
        merchant_id: int,
        date_from: Optional[datetime],
        date_to: Optional[datetime],
        granularity: Optional[str],
    ) -> dict:
        df, dt = _resolve_range(date_from, date_to, default_days=7)
        gran = granularity or _auto_granularity(df, dt)

        pg_points = await self.repository.get_merchant_order_timeseries(
            merchant_id, df, dt, gran
        )
        ch_points = await self.repository.payin_requests_timeseries_by_merchant(
            merchant_id, df, dt, gran
        )

        def _to_unix(d: datetime) -> int:
            aware = (
                d.replace(tzinfo=timezone.utc) if d.tzinfo is None
                else d.astimezone(timezone.utc)
            )
            return int(aware.timestamp())

        # Postgres date_trunc buckets are naive-UTC; ClickHouse buckets are unix.
        pg_by_ts = {_to_unix(p["ts"]): p for p in pg_points}
        req_by_ts = {int(p["bucket"]): int(p["requests"]) for p in ch_points}

        cursor = _bucket_truncate(df, gran)
        end = _bucket_truncate(dt, gran)
        max_buckets = 2000
        points: list = []
        while cursor <= end and len(points) < max_buckets:
            ts_unix = int(cursor.timestamp())
            pg = pg_by_ts.get(ts_unix)
            points.append({
                "ts": ts_unix,
                "requests": req_by_ts.get(ts_unix, 0),
                "created": int(pg["created"]) if pg else 0,
                "success": int(pg["success"]) if pg else 0,
            })
            cursor = _bucket_advance(cursor, gran)
        return {"granularity": gran, "points": points}

    # ── requisite activity («Активность» tab) ───────────────────────────────

    async def snapshot_requisite_activity(self) -> int:
        """Capture the currently-ready requisites into ClickHouse — one row per
        requisite, delivered as ONE batched insert per tick. Returns the number
        of rows written (0 = nothing ready / CH disabled). Fail-safe on the CH
        side; called once a minute by ``snapshot_requisite_activity_task``."""
        rows = await self.repository.list_ready_requisites_for_snapshot()
        if not rows:
            return 0
        ts = utcnow().replace(microsecond=0)
        records = [
            activity_ch.RequisiteActivitySnapshotRecord(
                requisite_id=r[0],
                trader_id=r[1],
                currency=getattr(r[2], "value", r[2]),
                limit_min=r[3],
                limit_max=r[4],
                limit_total=r[5],
                ts=ts,
            )
            for r in rows
        ]
        activity_ch.insert_snapshots(records)
        return len(records)

    async def get_activity_timeseries(
        self,
        date_from: Optional[datetime],
        date_to: Optional[datetime],
        granularity: Optional[str] = None,
        currency: Optional[str] = None,
    ) -> dict:
        """Requisite-availability chart for the «Активность» tab, Redis-cached
        60s (key includes every param). Defaults the range to the last 24h and
        the currency to RUB — one currency per chart (amounts across currencies
        can't share an axis)."""
        cur = (currency or "RUB").upper()
        cache_key = (
            f"stats:admin:activity:{_stamp(date_from)}:{_stamp(date_to)}:"
            f"{granularity or 'auto'}:{cur}"
        )
        return await cache_json(
            cache_key,
            _ACTIVITY_TTL,
            lambda: self._compute_activity_timeseries(date_from, date_to, granularity, cur),
        )

    async def _compute_activity_timeseries(
        self,
        date_from: Optional[datetime],
        date_to: Optional[datetime],
        granularity: Optional[str],
        currency: str,
    ) -> dict:
        from collections import defaultdict

        def _ensure_aware(d: Optional[datetime]) -> Optional[datetime]:
            if d is None:
                return None
            if d.tzinfo is None:
                return d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc)

        df = _ensure_aware(date_from)
        dt = _ensure_aware(date_to)
        if df is None and dt is None:
            dt = utcnow()
            df = dt - timedelta(hours=24)
        elif df is None:
            assert dt is not None
            df = dt - timedelta(hours=24)
        elif dt is None:
            dt = utcnow()

        if granularity is None:
            span = dt - df
            if span <= timedelta(hours=24):
                granularity = "hour"
            elif span <= timedelta(days=31):
                granularity = "day"
            elif span <= timedelta(days=180):
                granularity = "week"
            else:
                granularity = "month"

        rows = await activity_ch.query_activity(df, dt, currency, granularity)
        available = await activity_ch.available_currencies(df, dt)
        if currency not in available:
            available = [currency, *available]

        # Reduce CH's per-requisite-per-bucket rows to intervals + trader sets
        # per bucket (keyed by the bucket's unix second, as CH returned it).
        by_bucket: dict = defaultdict(list)
        traders: dict = defaultdict(set)
        for row in rows:
            b = int(row["bucket"])
            by_bucket[b].append((row["mn"], row["mx"]))
            traders[b].add(int(row["trader_id"]))

        def _truncate(ts: datetime, gran: str) -> datetime:
            if gran == "minute":
                return ts.replace(second=0, microsecond=0)
            if gran == "hour":
                return ts.replace(minute=0, second=0, microsecond=0)
            if gran == "3hour":
                return ts.replace(hour=(ts.hour // 3) * 3, minute=0, second=0, microsecond=0)
            if gran == "day":
                return ts.replace(hour=0, minute=0, second=0, microsecond=0)
            if gran == "week":
                start = ts - timedelta(days=ts.weekday())
                return start.replace(hour=0, minute=0, second=0, microsecond=0)
            return ts.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        def _advance(ts: datetime, gran: str) -> datetime:
            if gran == "minute":
                return ts + timedelta(minutes=1)
            if gran == "hour":
                return ts + timedelta(hours=1)
            if gran == "3hour":
                return ts + timedelta(hours=3)
            if gran == "day":
                return ts + timedelta(days=1)
            if gran == "week":
                return ts + timedelta(weeks=1)
            if ts.month == 12:
                return ts.replace(year=ts.year + 1, month=1, day=1)
            return ts.replace(month=ts.month + 1, day=1)

        cursor = _truncate(df, granularity)
        end = _truncate(dt, granularity)
        max_buckets = 2000
        buckets: list[dict] = []
        while cursor <= end and len(buckets) < max_buckets:
            ts_unix = int(cursor.timestamp())
            intervals = by_bucket.get(ts_unix)
            if intervals:
                bars = [
                    {"min": lo, "max": hi, "segments": _segments(lo, hi, intervals)}
                    for lo, hi in _merge_intervals(intervals)
                ]
                buckets.append({
                    "ts": ts_unix,
                    "trader_count": len(traders.get(ts_unix, ())),
                    "bars": bars,
                })
            else:
                buckets.append({"ts": ts_unix, "trader_count": 0, "bars": []})
            cursor = _advance(cursor, granularity)

        return {
            "granularity": granularity,
            "currency": currency,
            "available_currencies": available,
            "buckets": buckets,
        }


class ReceiptCheckStatsService(BaseService):
    """Admin «Чекер» stats: shape the receipt-check rollups + derived rates.

    Reuses the module-level range/granularity/backfill helpers shared with the
    other stats reads (``_stamp`` / ``_resolve_range`` / ``_auto_granularity`` /
    ``_bucket_truncate`` / ``_bucket_advance``)."""

    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = ReceiptCheckStatsRepository(session)

    @staticmethod
    def _rate(numer: float, denom: float) -> float:
        return (numer / denom) if denom else 0.0

    @classmethod
    def _provider_dict(cls, r: ProviderRollupRow) -> dict:
        return {
            "provider_id": r.provider_id,
            "checker": r.name or _UNKNOWN_PROVIDER,
            "adapter_type": r.adapter_type,
            "total": r.total, "success": r.success, "failed": r.failed,
            "manual": r.manual, "auto": r.auto,
            "clean": r.clean, "suspicious": r.suspicious,
            "suspicious_rate": cls._rate(r.suspicious, r.decided),
            "spent_usdt": r.charged_usdt,
            "refunded_usdt": r.refunded_usdt,
            "net_usdt": r.charged_usdt - r.refunded_usdt,
            "avg_price_usdt": cls._rate(r.charged_usdt, r.charged_count),
        }

    @classmethod
    def _trader_dict(cls, t: TraderRollupRow) -> dict:
        return {
            "trader_user_id": t.trader_user_id,
            "username": t.username,
            "checks": t.checks,
            "spent_usdt": t.charged_usdt,
            "suspicious_rate": cls._rate(t.suspicious, t.decided),
        }

    async def get_checker_stats(
        self, date_from: Optional[datetime], date_to: Optional[datetime]
    ) -> dict:
        """Totals + per-provider + top-traders rollup for the «Чекер» tab,
        Redis-cached 30s (key per range). Defaults the range to the last 7
        days — same UX as the sibling stats tabs."""
        cache_key = f"stats:admin:checkers:{_stamp(date_from)}:{_stamp(date_to)}"
        return await cache_json(
            cache_key,
            _CHECKER_STATS_TTL,
            lambda: self._compute_checker_stats(date_from, date_to),
        )

    async def _compute_checker_stats(
        self, date_from: Optional[datetime], date_to: Optional[datetime]
    ) -> dict:
        df, dt = _resolve_range(date_from, date_to, default_days=7)
        providers = await self.repository.provider_rollup(df, dt)
        traders = await self.repository.top_traders(df, dt)

        total = sum(p.total for p in providers)
        decided = sum(p.decided for p in providers)
        suspicious = sum(p.suspicious for p in providers)
        charged = sum(p.charged_usdt for p in providers)
        refunded = sum(p.refunded_usdt for p in providers)
        charged_count = sum(p.charged_count for p in providers)

        totals = {
            "total": total,
            "success": sum(p.success for p in providers),
            "failed": sum(p.failed for p in providers),
            "clean": sum(p.clean for p in providers),
            "suspicious": suspicious,
            "suspicious_rate": self._rate(suspicious, decided),
            "spent_usdt": charged,
            "refunded_usdt": refunded,
            "net_usdt": charged - refunded,
            "avg_price_usdt": self._rate(charged, charged_count),
        }
        return {
            "totals": totals,
            "providers": [self._provider_dict(p) for p in providers],
            "top_traders": [self._trader_dict(t) for t in traders],
        }

    async def get_checker_timeseries(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
        granularity: Optional[str],
    ) -> dict:
        """Checks/spend per bucket for the «Чекер» chart, Redis-cached 60s
        (key per range+granularity). Defaults the range to the last 7 days
        and, when ``granularity`` is omitted, auto-picks a bucket size from
        the span (same rule as the sibling admin timeseries)."""
        cache_key = (
            f"stats:admin:checkers:timeseries:{_stamp(date_from)}:{_stamp(date_to)}:"
            f"{granularity or 'auto'}"
        )
        return await cache_json(
            cache_key,
            _CHECKER_TIMESERIES_TTL,
            lambda: self._compute_checker_timeseries(date_from, date_to, granularity),
        )

    async def _compute_checker_timeseries(
        self, date_from: Optional[datetime], date_to: Optional[datetime],
        granularity: Optional[str],
    ) -> dict:
        df, dt = _resolve_range(date_from, date_to, default_days=7)
        gran = granularity or _auto_granularity(df, dt)

        rows = await self.repository.timeseries(df, dt, gran)

        # Repo bucket timestamps come from Postgres ``date_trunc``/``date_bin``
        # and are naive UTC; normalise the same way our walker generates them
        # so the equality lookup in ``by_ts`` works.
        def _normalise_repo_ts(d: datetime) -> datetime:
            if d.tzinfo is None:
                return d.replace(tzinfo=timezone.utc)
            return d.astimezone(timezone.utc)

        by_ts = {_normalise_repo_ts(r.ts): r for r in rows}

        cursor = _bucket_truncate(df, gran)
        end = _bucket_truncate(dt, gran)
        # Hard cap to guard against pathologically long ranges.
        max_buckets = 2000
        out_points: list[dict] = []
        while cursor <= end and len(out_points) < max_buckets:
            existing = by_ts.get(cursor)
            ts_unix = int(cursor.timestamp())
            if existing is not None:
                out_points.append({
                    "ts": ts_unix,
                    "checks": existing.checks,
                    "spent_usdt": existing.spent_usdt,
                })
            else:
                out_points.append({
                    "ts": ts_unix,
                    "checks": 0,
                    "spent_usdt": 0.0,
                })
            cursor = _bucket_advance(cursor, gran)

        return {"granularity": gran, "points": out_points}
