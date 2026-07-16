"""Unit tests for the achievements engine (pure, DB-free) — the unified
``streak_volume_tier`` bonus: the streak GATE, the average-turnover LEVEL, rule
stacking, the global cap, and the fail-safe skips (unknown type / broken rule)."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.modules.achievements.metrics import TraderMetrics
from app.modules.achievements.service import AchievementService

TODAY = date(2026, 7, 7)

# A 3-day streak (each day ≥ 100) unlocks a bonus sized by the AVERAGE daily
# turnover over those 3 days.
RULE = {
    "type": "streak_volume_tier",
    "streak_days": 3,
    "min_daily_volume": "100",
    "tiers": [
        {"min_avg": "1000", "percent": "0.5"},
        {"min_avg": "5000", "percent": "1.5"},
        {"min_avg": "10000", "percent": "3"},
    ],
}


def _metrics(volume_by_offset):
    """``volume_by_offset``: {N: volume} where offset N = N days before today
    (0 = today, 1 = yesterday)."""
    dv = {TODAY - timedelta(days=off): Decimal(str(v)) for off, v in volume_by_offset.items()}
    return TraderMetrics(trader_user_id=1, today=TODAY, daily_volume_usdt=dv)


def _bonus(metrics, rules, cap="0"):
    total, _ = AchievementService(session=MagicMock()).evaluate_bonus(metrics, rules, Decimal(cap))
    return total


# ── streak gate ───────────────────────────────────────────────────────────
def test_streak_not_reached_no_bonus():
    # Only 2 qualifying days (today + yesterday); need 3 → bonus 0 even at high avg.
    assert _bonus(_metrics({0: 8000, 1: 8000}), [RULE]) == Decimal("0")


def test_below_min_daily_breaks_streak():
    # day-1 is below min_daily (100) → streak from today = 1 → not reached → 0.
    assert _bonus(_metrics({0: 8000, 1: 50, 2: 8000, 3: 8000}), [RULE]) == Decimal("0")


def test_zero_activity_day_breaks_streak():
    assert _bonus(_metrics({0: 8000, 1: 0, 2: 8000}), [RULE]) == Decimal("0")


# ── level by average (streak reached) ─────────────────────────────────────
@pytest.mark.parametrize(
    "vol,expected",
    [("300", "0"), ("1000", "0.5"), ("6000", "1.5"), ("12000", "3"), ("10000", "3")],
)
def test_level_by_average(vol, expected):
    # 3 qualifying days at the same volume → average == that volume → its tier.
    assert _bonus(_metrics({0: vol, 1: vol, 2: vol}), [RULE]) == Decimal(expected)


def test_average_across_uneven_days():
    # 3 days [3000, 6000, 9000] (all ≥ min_daily) → streak ok, avg 6000 → tier 1.5.
    assert _bonus(_metrics({0: 3000, 1: 6000, 2: 9000}), [RULE]) == Decimal("1.5")


def test_reached_streak_but_avg_below_first_tier():
    # Qualifying (≥100) but avg 300 < first tier 1000 → no level → 0.
    assert _bonus(_metrics({0: 300, 1: 300, 2: 300}), [RULE]) == Decimal("0")


# ── engine: stacking + cap + fail-safe ────────────────────────────────────
RULE2 = {
    "type": "streak_volume_tier",
    "streak_days": 2,
    "min_daily_volume": "100",
    "tiers": [{"min_avg": "100", "percent": "0.3"}],
}


def test_rules_stack():
    # RULE: 3 days avg 6000 → 1.5; RULE2: 2 days avg 6000 ≥ 100 → 0.3 → total 1.8.
    metrics = _metrics({0: 6000, 1: 6000, 2: 6000})
    assert _bonus(metrics, [RULE, RULE2]) == Decimal("1.8")


def test_cap_clamps_total():
    metrics = _metrics({0: 6000, 1: 6000, 2: 6000})
    assert _bonus(metrics, [RULE, RULE2], cap="1.0") == Decimal("1.0")


def test_unknown_rule_type_is_skipped():
    assert _bonus(_metrics({0: 8000, 1: 8000, 2: 8000}), [{"type": "not_a_rule"}]) == Decimal("0")


def test_broken_rule_does_not_poison_others():
    # Malformed rule (streak_days not a number, tiers not a list) is skipped; RULE applies.
    broken = {"type": "streak_volume_tier", "streak_days": "x", "tiers": "not-a-list"}
    assert _bonus(_metrics({0: 6000, 1: 6000, 2: 6000}), [broken, RULE]) == Decimal("1.5")
