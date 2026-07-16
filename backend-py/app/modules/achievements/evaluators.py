"""Rule evaluators + registry — the extensible core of the achievements engine.

One achievement bonus = a STREAK gate + a turnover LEVEL. The trader unlocks the
bonus by keeping a streak of ``streak_days`` consecutive days (today-inclusive)
each with turnover ≥ ``min_daily_volume``; once unlocked, the bonus SIZE is the
tier the AVERAGE daily turnover over those ``streak_days`` days falls into. Streak
not reached → bonus 0.

Each ``AchievementRuleType`` maps to one ``RuleEvaluator``; the engine
(``AchievementService``) iterates the configured rules and dispatches each via
``REGISTRY`` — adding a new kind of bonus is a new evaluator class + one registry
entry, with no change to the engine, storage, or hot path.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional

from app.common.enums.achievements import AchievementRuleType
from app.modules.achievements.metrics import TraderMetrics

logger = logging.getLogger(__name__)


def _dec(value: Any, default: str = "0") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


@dataclass
class RuleOutcome:
    """Result of evaluating one rule for a trader."""

    rule_type: AchievementRuleType
    bonus_percent: Decimal
    level_key: Optional[str]  # which level was reached (for the UI); None if nothing


@dataclass
class StreakTierResult:
    """Full state of a ``streak_volume_tier`` rule for one trader — the money
    outcome (``bonus_percent``) plus everything the cabinet widgets render (streak
    progress + the level bar). Computed once, reused by the evaluator and the view.
    """

    streak_days: int  # X — required streak length AND the averaging window
    streak_count: int  # current consecutive qualifying days (capped at X)
    active: bool  # streak_count >= streak_days → the level bonus is unlocked
    min_daily_volume: Decimal  # a day counts toward the streak if its volume ≥ this
    avg_volume: Decimal  # mean daily turnover over the last X days (today-inclusive)
    tiers: List[Dict[str, Decimal]]  # [{min_avg, percent}] sorted ascending
    current_index: int  # highest tier the avg reaches; -1 if below the first
    tier_percent: Decimal  # the reached tier's percent (0 if none)
    bonus_percent: Decimal  # tier_percent if active else 0 (the materialized bonus)


def evaluate_streak_volume_tier(
    params: Dict[str, Any], metrics: TraderMetrics
) -> Optional[StreakTierResult]:
    """Pure evaluation of a ``streak_volume_tier`` rule. Returns ``None`` for an
    unusable config (missing/invalid ``streak_days``) so callers fail-safe."""
    try:
        streak_days = int(_dec(params.get("streak_days")))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if streak_days <= 0:
        return None

    min_daily = _dec(params.get("min_daily_volume"))
    tiers = sorted(
        (
            {"min_avg": _dec(t.get("min_avg")), "percent": _dec(t.get("percent"))}
            for t in (params.get("tiers") or [])
            if isinstance(t, dict)
        ),
        key=lambda t: t["min_avg"],
    )

    # Streak: consecutive qualifying days walking back from TODAY (today-inclusive),
    # capped at streak_days (length beyond the target is moot). A day qualifies with
    # real activity (volume > 0) AND volume ≥ min_daily, so a no-activity day always
    # breaks the streak even when min_daily = 0.
    streak_count = 0
    day = metrics.today
    while streak_count < streak_days:
        vol = metrics.volume_on(day)
        if vol > 0 and vol >= min_daily:
            streak_count += 1
            day -= timedelta(days=1)
        else:
            break
    active = streak_count >= streak_days

    # Average daily turnover over the last X days (today-inclusive); zero/low days
    # are included in the mean. When active, all X days qualified so avg ≥ min_daily.
    total = sum(
        (metrics.volume_on(metrics.today - timedelta(days=i)) for i in range(streak_days)),
        Decimal(0),
    )
    avg_volume = total / streak_days

    current_index = -1
    for i, tier in enumerate(tiers):
        if avg_volume >= tier["min_avg"]:
            current_index = i
    tier_percent = tiers[current_index]["percent"] if current_index >= 0 else Decimal(0)
    bonus_percent = tier_percent if active else Decimal(0)

    return StreakTierResult(
        streak_days=streak_days,
        streak_count=streak_count,
        active=active,
        min_daily_volume=min_daily,
        avg_volume=avg_volume,
        tiers=tiers,
        current_index=current_index,
        tier_percent=tier_percent,
        bonus_percent=bonus_percent,
    )


class RuleEvaluator(ABC):
    rule_type: AchievementRuleType

    @abstractmethod
    def evaluate(self, params: Dict[str, Any], metrics: TraderMetrics) -> RuleOutcome:
        """Turn a rule's config dict + the trader's ``TraderMetrics`` into a ``RuleOutcome``."""
        ...


class StreakVolumeTierEvaluator(RuleEvaluator):
    """Streak-gated, average-volume tier — see the module docstring."""

    rule_type = AchievementRuleType.STREAK_VOLUME_TIER

    def evaluate(self, params: Dict[str, Any], metrics: TraderMetrics) -> RuleOutcome:
        result = evaluate_streak_volume_tier(params, metrics)
        if result is None or result.bonus_percent <= 0:
            return RuleOutcome(self.rule_type, Decimal(0), None)
        level_key = f"{result.streak_days}d·{result.tier_percent:g}%"
        return RuleOutcome(self.rule_type, result.bonus_percent, level_key)


_EVALUATORS: List[RuleEvaluator] = [StreakVolumeTierEvaluator()]
REGISTRY: Dict[AchievementRuleType, RuleEvaluator] = {e.rule_type: e for e in _EVALUATORS}


def get_evaluator(rule_type: Optional[str]) -> Optional[RuleEvaluator]:
    """Resolve a rule ``type`` string (from config) to its evaluator, or ``None``
    for an unknown/legacy type — the engine skips unknown types (forward-compatible)."""
    if not rule_type:
        return None
    try:
        rt = AchievementRuleType(rule_type)
    except ValueError:
        return None
    return REGISTRY.get(rt)
