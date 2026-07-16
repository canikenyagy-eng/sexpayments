"""Data access for the achievements feature.

All queries are off the hot path (called only by the background workers): a
bounded one-day GROUP BY over ``orders`` for the rollup, and small reads over the
rollup / trader tables for evaluation + materialization. Portable (no
PG-only upsert) so it runs the same on the SQLite test engine."""
from __future__ import annotations

from datetime import date as date_cls
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.achievements import AchievementRuleType
from app.common.enums.orders import OrderStatus
from app.modules.achievements.models import TraderAchievement, TraderDailyVolume
from app.modules.orders.models import Order
from app.modules.traders.models import Trader


class AchievementRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── daily-volume rollup ───────────────────────────────────────────────
    async def aggregate_orders_for_day(
        self, day_start: datetime, day_end: datetime
    ) -> List[Tuple[int, Decimal, Decimal, int]]:
        """Per-trader ``(trader_user_id, rub, usdt, count)`` of SUCCESS orders with
        ``created_at ∈ [day_start, day_end)``. Bounded to one day — uses the
        ``ix_orders_status_created`` index instead of scanning all of ``orders``."""
        stmt = (
            select(
                Order.trader_id,
                func.coalesce(func.sum(Order.amount), 0),
                func.coalesce(func.sum(Order.amount_usdt), 0),
                func.count(),
            )
            .where(
                Order.status == OrderStatus.SUCCESS,
                Order.trader_id.isnot(None),
                Order.created_at >= day_start,
                Order.created_at < day_end,
            )
            .group_by(Order.trader_id)
        )
        rows = (await self.session.execute(stmt)).all()
        return [(r[0], Decimal(r[1] or 0), Decimal(r[2] or 0), int(r[3] or 0)) for r in rows]

    async def get_daily_row(self, trader_user_id: int, day: date_cls) -> Optional[TraderDailyVolume]:
        return (await self.session.execute(
            select(TraderDailyVolume).where(
                TraderDailyVolume.trader_user_id == trader_user_id,
                TraderDailyVolume.date == day,
            )
        )).scalar_one_or_none()

    async def get_daily_rows_for_date(self, day: date_cls) -> Dict[int, TraderDailyVolume]:
        stmt = select(TraderDailyVolume).where(TraderDailyVolume.date == day)
        rows = (await self.session.execute(stmt)).scalars().all()
        return {r.trader_user_id: r for r in rows}

    def upsert_daily_volume(
        self,
        existing: Dict[int, TraderDailyVolume],
        trader_user_id: int,
        day: date_cls,
        amount_usdt: Decimal,
        amount_rub: Decimal,
        order_count: int,
    ) -> None:
        """Set the (trader, day) rollup to the freshly aggregated totals. Full-day
        SET (not increment), so re-running a tick is idempotent. ``existing`` is a
        preloaded {trader_user_id: row} for the day to avoid a per-trader SELECT."""
        row = existing.get(trader_user_id)
        if row is None:
            row = TraderDailyVolume(trader_user_id=trader_user_id, date=day)
            self.session.add(row)
            existing[trader_user_id] = row
        row.amount_usdt = amount_usdt
        row.amount_rub = amount_rub
        row.order_count = order_count

    async def load_volume_window(self, since: date_cls) -> Dict[int, Dict[date_cls, Decimal]]:
        """``{trader_user_id: {date: amount_usdt}}`` for all days ≥ ``since``. One
        query; the engine evaluates each trader in memory."""
        stmt = select(
            TraderDailyVolume.trader_user_id,
            TraderDailyVolume.date,
            TraderDailyVolume.amount_usdt,
        ).where(TraderDailyVolume.date >= since)
        out: Dict[int, Dict[date_cls, Decimal]] = {}
        for tid, d, amt in (await self.session.execute(stmt)).all():
            out.setdefault(tid, {})[d] = Decimal(amt or 0)
        return out

    async def load_trader_volume_window(
        self, trader_user_id: int, since: date_cls
    ) -> Dict[date_cls, Decimal]:
        """``{date: amount_usdt}`` for ONE trader, all days ≥ ``since`` — for the
        cabinet progress view (streak count / tier), a handful of rows."""
        stmt = select(TraderDailyVolume.date, TraderDailyVolume.amount_usdt).where(
            TraderDailyVolume.trader_user_id == trader_user_id,
            TraderDailyVolume.date >= since,
        )
        return {d: Decimal(a or 0) for d, a in (await self.session.execute(stmt)).all()}

    # ── materialized bonus + breakdown ────────────────────────────────────
    async def get_all_trader_bonuses(self) -> Dict[int, Decimal]:
        """``{trader user_id: current achievement_bonus_percent}`` — to write only
        the traders whose bonus actually changed."""
        rows = (await self.session.execute(
            select(Trader.user_id, Trader.achievement_bonus_percent)
        )).all()
        return {uid: Decimal(b or 0) for uid, b in rows}

    async def get_trader_bonus(self, trader_user_id: int) -> Decimal:
        row = (await self.session.execute(
            select(Trader.achievement_bonus_percent).where(Trader.user_id == trader_user_id)
        )).scalar_one_or_none()
        return Decimal(row or 0)

    async def set_trader_bonus(self, trader_user_id: int, bonus: Decimal) -> None:
        await self.session.execute(
            update(Trader)
            .where(Trader.user_id == trader_user_id)
            .values(achievement_bonus_percent=bonus)
        )

    async def get_achievements_for_trader(self, trader_user_id: int) -> Dict[AchievementRuleType, TraderAchievement]:
        rows = (await self.session.execute(
            select(TraderAchievement).where(TraderAchievement.trader_user_id == trader_user_id)
        )).scalars().all()
        return {r.rule_type: r for r in rows}

    async def replace_achievements(
        self,
        trader_user_id: int,
        outcomes: List[Tuple[AchievementRuleType, str, Decimal]],
    ) -> None:
        """Reconcile a trader's per-rule breakdown to exactly ``outcomes``
        ((rule_type, level_key, bonus_percent)): upsert present rules, drop the rest."""
        existing = await self.get_achievements_for_trader(trader_user_id)
        keep: set = set()
        for rule_type, level_key, bonus_percent in outcomes:
            keep.add(rule_type)
            row = existing.get(rule_type)
            if row is None:
                row = TraderAchievement(trader_user_id=trader_user_id, rule_type=rule_type)
                self.session.add(row)
            row.level_key = level_key
            row.bonus_percent = bonus_percent
        for rule_type, row in existing.items():
            if rule_type not in keep:
                await self.session.delete(row)
