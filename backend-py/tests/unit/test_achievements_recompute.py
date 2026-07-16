"""Unit tests for AchievementService.recompute_bonuses — the materialization
step: it must write ONLY traders whose bonus changed, and reset stale bonuses to
0 when the feature is disabled. The pure formula is covered separately in
test_achievements_engine."""
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.achievements.service import AchievementService

TODAY = date(2026, 7, 7)

# 3-day streak (each ≥ 1000) unlocks a level sized by the average; avg ≥ 1000 → +0.1.
RULE = {
    "type": "streak_volume_tier",
    "streak_days": 3,
    "min_daily_volume": "1000",
    "tiers": [{"min_avg": "1000", "percent": "0.1"}],
}

# 3 qualifying days ending today (today-inclusive) → streak reached, avg 1500 → +0.1.
_WINDOW_3D = {
    TODAY: Decimal(1500),
    TODAY - timedelta(days=1): Decimal(1500),
    TODAY - timedelta(days=2): Decimal(1500),
}


def _patched(*, enabled, rules, cap, window, bonuses):
    """Patch SettingsService + AchievementRepository used inside recompute."""
    settings = MagicMock()
    settings.get_bool = AsyncMock(return_value=enabled)
    settings.get_decimal = AsyncMock(return_value=Decimal(cap))
    settings.get_json = AsyncMock(return_value=rules)

    repo = MagicMock()
    repo.load_volume_window = AsyncMock(return_value=window)
    repo.get_all_trader_bonuses = AsyncMock(return_value=bonuses)
    repo.set_trader_bonus = AsyncMock()
    repo.replace_achievements = AsyncMock()
    return settings, repo


async def _run(settings, repo):
    with patch("app.modules.achievements.service.SettingsService", return_value=settings), \
         patch("app.modules.achievements.service.AchievementRepository", return_value=repo):
        svc = AchievementService(session=MagicMock())
        changed = await svc.recompute_bonuses(today=TODAY)
    return changed


@pytest.mark.asyncio
async def test_writes_only_changed_trader():
    # trader 1: 3-day streak, avg 1500 → 0.1 (new); trader 2: no activity, had 0.1 → reset.
    settings, repo = _patched(
        enabled=True, rules=[RULE], cap="0", window={1: _WINDOW_3D}, bonuses={1: Decimal(0), 2: Decimal("0.1")}
    )
    changed = await _run(settings, repo)

    assert changed == 2
    written = {c.args[0]: c.args[1] for c in repo.set_trader_bonus.await_args_list}
    assert written == {1: Decimal("0.1"), 2: Decimal("0")}


@pytest.mark.asyncio
async def test_no_write_when_unchanged():
    settings, repo = _patched(
        enabled=True, rules=[RULE], cap="0", window={1: _WINDOW_3D}, bonuses={1: Decimal("0.1")}
    )
    changed = await _run(settings, repo)
    assert changed == 0
    repo.set_trader_bonus.assert_not_awaited()


@pytest.mark.asyncio
async def test_streak_not_reached_resets_bonus():
    # Only 2 qualifying days → streak not reached → bonus 0 (was 0.1 → written).
    window = {1: {TODAY: Decimal(1500), TODAY - timedelta(days=1): Decimal(1500)}}
    settings, repo = _patched(
        enabled=True, rules=[RULE], cap="0", window=window, bonuses={1: Decimal("0.1")}
    )
    changed = await _run(settings, repo)
    assert changed == 1
    written = {c.args[0]: c.args[1] for c in repo.set_trader_bonus.await_args_list}
    assert written == {1: Decimal("0")}


@pytest.mark.asyncio
async def test_disabled_resets_existing_bonuses():
    # Feature off → rules empty → any non-zero bonus is cleared to 0.
    settings, repo = _patched(
        enabled=False, rules=[], cap="0", window={}, bonuses={7: Decimal("0.3"), 8: Decimal(0)}
    )
    changed = await _run(settings, repo)
    assert changed == 1
    written = {c.args[0]: c.args[1] for c in repo.set_trader_bonus.await_args_list}
    assert written == {7: Decimal("0")}
