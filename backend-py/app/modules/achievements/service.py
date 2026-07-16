"""Achievements engine.

The pure evaluation core lives here: given a trader's metrics, the configured
rules (from the ``achievement_rules`` platform setting) and the global cap, it
returns the total bonus % and the per-rule breakdown. It is deterministic and
DB-free — the daily/5-minute worker jobs (see ``app/workers/tasks/achievements``)
load the metrics, call this, then materialize the result into
``traders.achievement_bonus_percent`` (hot path) + ``trader_achievements``
(breakdown). Keeping compute separate from I/O keeps the money-affecting formula
trivially unit-testable.
"""
from __future__ import annotations

import logging
from datetime import date as date_cls
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.enums.achievements import AchievementRuleType
from app.common.types import utcnow
from app.modules.achievements.evaluators import (
    RuleOutcome,
    evaluate_streak_volume_tier,
    get_evaluator,
)
from app.modules.achievements.metrics import TraderMetrics
from app.modules.achievements.repository import AchievementRepository
from app.modules.base.service import BaseService
from app.modules.settings.service import SettingsService

logger = logging.getLogger(__name__)


def _num(value: Any, default: float = 0.0) -> float:
    """Parse a config value (str/number) to float for the progress view."""
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return default


def _max_streak_lookback_days(rules: Optional[List[Dict[str, Any]]]) -> int:
    """Longest streak window any configured rule needs — the recompute/view only
    load that many days of rollup (the streak/average never look back further).
    +2 days buffer."""
    best = 1
    for rule in rules or []:
        if isinstance(rule, dict) and rule.get("type") == AchievementRuleType.STREAK_VOLUME_TIER.value:
            best = max(best, int(_num(rule.get("streak_days"))))
    return best + 2


def _rule_progress(
    rule: Dict[str, Any], metrics: TraderMetrics
) -> Optional[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Cabinet widget data for one ``streak_volume_tier`` rule: the level bar
    (avg-over-X-days tiers, locked until the streak is reached) + the streak ring.
    Returns ``(tier, streak)`` dicts, or ``None`` if the rule is unusable."""
    result = evaluate_streak_volume_tier(rule, metrics)
    if result is None:
        return None
    tier = {
        "window_days": result.streak_days,
        "avg_volume_usdt": float(result.avg_volume),
        "current_index": result.current_index,
        "current_percent": float(result.tier_percent),
        "locked": not result.active,
        "levels": [
            {"threshold_usdt": float(t["min_avg"]), "percent": float(t["percent"])}
            for t in result.tiers
        ],
    }
    streak = {
        "current_days": result.streak_count,
        "target_days": result.streak_days,
        "active": result.active,
        "min_volume_usdt": float(result.min_daily_volume),
        "bonus_percent": float(result.tier_percent),
    }
    return tier, streak


class AchievementService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = AchievementRepository(session)
        self.settings = SettingsService(session)

    def evaluate_bonus(
        self,
        metrics: TraderMetrics,
        rules: Optional[List[Dict[str, Any]]],
        cap: Decimal = Decimal(0),
    ) -> Tuple[Decimal, List[RuleOutcome]]:
        """Sum the bonus contributions of every configured rule, clamped to ``cap``.

        ``rules`` is the decoded ``achievement_rules`` config — a list of
        ``{"type": <AchievementRuleType>, ...params}`` dicts. Unknown types and
        individual evaluator errors are skipped (fail-safe: a bad rule never
        poisons the whole payout). ``cap`` ≤ 0 means "no cap". Returns
        ``(total_percent, [outcomes with a positive bonus])``.
        """
        outcomes: List[RuleOutcome] = []
        for rule in rules or []:
            evaluator = get_evaluator(rule.get("type"))
            if evaluator is None:
                continue  # unknown rule type → forward-compatible skip
            try:
                outcome = evaluator.evaluate(rule, metrics)
            except Exception:  # noqa: BLE001 — one broken rule must not void the rest
                logger.exception(
                    "achievement rule evaluation failed (type=%s, trader=%s)",
                    rule.get("type"),
                    metrics.trader_user_id,
                )
                continue
            if outcome.bonus_percent > 0:
                outcomes.append(outcome)

        total = sum((o.bonus_percent for o in outcomes), Decimal(0))
        if cap and cap > 0:
            total = min(total, cap)
        return total, outcomes

    # ── DB glue (called by the workers, off the hot path) ─────────────────
    async def refresh_volume_for_day(self, day: date_cls) -> int:
        """Aggregate SUCCESS orders for ``day`` and SET the per-trader rollup rows
        to those totals (idempotent). Returns the number of traders with activity
        that day."""
        start = datetime.combine(day, time.min, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        agg = await self.repository.aggregate_orders_for_day(start, end)
        existing = await self.repository.get_daily_rows_for_date(day)
        for trader_user_id, rub, usdt, count in agg:
            self.repository.upsert_daily_volume(existing, trader_user_id, day, usdt, rub, count)
        return len(agg)

    async def recompute_bonuses(self, *, today: Optional[date_cls] = None) -> int:
        """Re-evaluate every trader's bonus from the rollup window and materialize
        it into ``traders.achievement_bonus_percent`` (+ the ``trader_achievements``
        breakdown), writing ONLY traders whose bonus changed. When the feature is
        off, rules collapse to empty → every stale bonus resets to 0. Returns the
        number of traders whose bonus changed."""
        enabled = await self.settings.get_bool("achievements_enabled")
        cap = await self.settings.get_decimal("achievement_bonus_max_percent")
        rules = await self.settings.get_json("achievement_rules") if enabled else []
        if not isinstance(rules, list):
            rules = []

        today = today or utcnow().date()
        window = await self.repository.load_volume_window(today - timedelta(days=_max_streak_lookback_days(rules)))
        current = await self.repository.get_all_trader_bonuses()

        # Evaluate every trader with recent volume OR a non-zero bonus to clear.
        trader_ids = set(window) | {uid for uid, bonus in current.items() if bonus and bonus > 0}
        changed = 0
        for trader_user_id in trader_ids:
            metrics = TraderMetrics(
                trader_user_id=trader_user_id,
                today=today,
                daily_volume_usdt=window.get(trader_user_id, {}),
            )
            total, outcomes = self.evaluate_bonus(metrics, rules, cap)
            if total != current.get(trader_user_id, Decimal(0)):
                await self.repository.set_trader_bonus(trader_user_id, total)
                await self.repository.replace_achievements(
                    trader_user_id,
                    [(o.rule_type, o.level_key or "", o.bonus_percent) for o in outcomes],
                )
                changed += 1
        return changed

    async def recompute_now(self) -> int:
        """Refresh today's rollup + recompute all bonuses immediately (admin/ops
        trigger — same work the 5-minute job does). Returns #bonuses changed."""
        today = utcnow().date()
        await self.refresh_volume_for_day(today)
        return await self.recompute_bonuses(today=today)

    async def get_trader_view(self, trader_user_id: int, *, today: Optional[date_cls] = None) -> Dict[str, Any]:
        """Cabinet «Достижения» view: the trader's current materialized bonus,
        today's turnover (USDT), the per-rule unlocked breakdown, and — for the
        dashboard widgets — the FULL ladders + live progress of every configured
        tier / streak rule (adaptive: one entry per rule, empty when none)."""
        today = today or utcnow().date()
        enabled = await self.settings.get_bool("achievements_enabled")
        bonus = await self.repository.get_trader_bonus(trader_user_id)
        achievements = await self.repository.get_achievements_for_trader(trader_user_id)
        today_row = await self.repository.get_daily_row(trader_user_id, today)

        rules = await self.settings.get_json("achievement_rules") if enabled else []
        if not isinstance(rules, list):
            rules = []
        # Small window (top streak length + buffer) of THIS trader's rollup — feeds
        # the streak walk-back and the tier's driving-day volume.
        window = await self.repository.load_trader_volume_window(
            trader_user_id, today - timedelta(days=_max_streak_lookback_days(rules))
        )
        metrics = TraderMetrics(trader_user_id=trader_user_id, today=today, daily_volume_usdt=window)
        # Each streak_volume_tier rule yields one linked pair — the streak ring
        # (progress to X days) and the level bar (avg-over-X-days, locked until the
        # streak is reached). Both are the SAME bonus, shown from two angles.
        tiers: List[Dict[str, Any]] = []
        streaks: List[Dict[str, Any]] = []
        for rule in rules:
            rule_type = rule.get("type") if isinstance(rule, dict) else None
            if rule_type == AchievementRuleType.STREAK_VOLUME_TIER.value:
                progress = _rule_progress(rule, metrics)
                if progress:
                    tier, streak = progress
                    tiers.append(tier)
                    streaks.append(streak)

        return {
            "enabled": enabled,
            "total_bonus_percent": float(bonus),
            "today_volume_usdt": float(today_row.amount_usdt) if today_row else 0.0,
            "items": [
                {
                    "rule_type": row.rule_type,
                    "level_key": row.level_key,
                    "bonus_percent": float(row.bonus_percent),
                }
                for row in achievements.values()
            ],
            "tiers": tiers,
            "streaks": streaks,
        }
