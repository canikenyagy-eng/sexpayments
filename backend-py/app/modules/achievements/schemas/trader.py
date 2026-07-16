"""Trader-facing achievements schemas (cabinet «Достижения» block)."""
from typing import List, Optional

from app.common.enums.achievements import AchievementRuleType
from app.modules.base.schemas import BaseSchema


class TraderAchievementItem(BaseSchema):
    """One currently-unlocked achievement (per rule) with its bonus contribution."""

    rule_type: AchievementRuleType
    level_key: Optional[str] = None
    bonus_percent: float


class TraderTierLevel(BaseSchema):
    """One level of the tier ladder, as a progress step (by average turnover)."""

    threshold_usdt: float
    percent: float


class TraderTierProgress(BaseSchema):
    """The level bar of a ``streak_volume_tier`` rule: the average turnover over the
    streak window, the ladder of levels, the reached level, and whether the bonus is
    ``locked`` (the streak isn't reached yet, so the level bonus isn't applied)."""

    window_days: int  # X — the average is over this many days (= streak length)
    avg_volume_usdt: float
    current_index: int  # 0-based reached level; -1 if below the first
    current_percent: float
    locked: bool  # true until the streak is reached → the level bonus isn't active
    levels: List[TraderTierLevel]  # ascending by threshold


class TraderStreakProgress(BaseSchema):
    """The streak ring of a ``streak_volume_tier`` rule: how many consecutive
    qualifying days out of the required ``target_days``, whether it's ``active``
    (reached), and the bonus it unlocks (the level amount)."""

    current_days: int
    target_days: int  # X — consecutive qualifying days needed to unlock the bonus
    active: bool
    min_volume_usdt: float  # a day counts toward the streak if its turnover ≥ this
    bonus_percent: float  # the level bonus this streak unlocks


class TraderAchievementsResponse(BaseSchema):
    enabled: bool
    total_bonus_percent: float
    today_volume_usdt: float
    items: List[TraderAchievementItem]
    tiers: List[TraderTierProgress]
    streaks: List[TraderStreakProgress]
