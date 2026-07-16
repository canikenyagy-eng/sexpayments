"""Integration test for the achievements feature against a REAL session/DB:
seed SUCCESS orders across days → run the worker service methods
(refresh_volume_for_day + recompute_bonuses) → assert the daily-volume rollup,
the materialized ``traders.achievement_bonus_percent`` (streak-gated level by the
AVERAGE turnover over the streak window, capped), the per-rule breakdown, and the
cabinet view. Exercises the real aggregate SQL + upserts + config read (json
platform setting)."""
import json
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.achievements.models import TraderAchievement, TraderDailyVolume
from app.modules.achievements.service import AchievementService
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.settings.models import PlatformSetting
from app.modules.settings.service import SettingsService
from app.modules.traders.models import Trader
from app.modules.users.models import User

TODAY = date(2026, 7, 7)

# One unified rule: 3-day streak (each day ≥ 100) unlocks a level by the AVERAGE
# turnover over those 3 days (≥100 → +0.1, ≥500 → +0.3).
RULES = [
    {
        "type": "streak_volume_tier",
        "streak_days": 3,
        "min_daily_volume": "100",
        "tiers": [
            {"min_avg": "100", "percent": "0.1"},
            {"min_avg": "500", "percent": "0.3"},
        ],
    },
]


async def _mk_trader(session) -> User:
    u = User(username=f"tr_{uuid4().hex[:8]}", password=get_password_hash("pass12345"), role=UserRole.TRADER)
    session.add(u)
    await session.flush()
    session.add(Trader(user_id=u.id, is_payin_active=True))
    await session.flush()
    return u


async def _mk_merchant(session) -> Merchant:
    owner = User(username=f"m_{uuid4().hex[:8]}", password=get_password_hash("pass12345"), role=UserRole.MERCHANT)
    session.add(owner)
    await session.flush()
    suffix = uuid4().hex[:6]
    m = Merchant(
        user_id=owner.id, name=f"M-{suffix}", status=TerminalStatus.ENABLED,
        currency=Currency.RUB, api_key=f"key-{suffix}", api_secret=f"secret-{suffix}",
    )
    session.add(m)
    await session.flush()
    return m


async def _mk_order(session, *, merchant_id, trader_user_id, day: date, amount_usdt: Decimal):
    o = Order(
        uuid=uuid4(), external_id=f"ext-{uuid4().hex[:8]}",
        merchant_id=merchant_id, trader_id=trader_user_id,
        direction=PaymentDirection.PAYIN, payment_method=PaymentMethod.SBP,
        amount=Decimal("1000.00"), currency=Currency.RUB, exchange_rate=Decimal("10"),
        amount_usdt=amount_usdt, fee_usdt=Decimal("0"), trader_fee_usdt=Decimal("0"),
        profit_usdt=Decimal("0"), status=OrderStatus.SUCCESS,
        created_at=datetime.combine(day, time(12, 0), tzinfo=timezone.utc),
    )
    session.add(o)
    await session.flush()
    return o


def _enable(session):
    session.add(PlatformSetting(key="achievements_enabled", value="true"))
    session.add(PlatformSetting(key="achievement_bonus_max_percent", value="0"))
    session.add(PlatformSetting(key="achievement_rules", value=json.dumps(RULES)))


@pytest.mark.asyncio
async def test_orders_to_rollup_to_materialized_bonus(session):
    _enable(session)
    trader = await _mk_trader(session)
    merchant = await _mk_merchant(session)

    # 3 qualifying days ending today (today-inclusive), 600 USDT each → streak of 3.
    for offset in (0, 1, 2):
        await _mk_order(session, merchant_id=merchant.id, trader_user_id=trader.id,
                        day=TODAY - timedelta(days=offset), amount_usdt=Decimal("600"))
    await session.flush()

    svc = AchievementService(session)
    for offset in (0, 1, 2):
        await svc.refresh_volume_for_day(TODAY - timedelta(days=offset))
    changed = await svc.recompute_bonuses(today=TODAY)
    await session.flush()

    # rollup rows exist with the aggregated USDT.
    rows = {r.date: r for r in (await session.execute(select(TraderDailyVolume))).scalars().all()}
    assert rows[TODAY].amount_usdt == Decimal("600.0000")
    assert rows[TODAY].order_count == 1

    # materialized bonus: streak reached (3 days) → level by avg 600 (≥500) = 0.3.
    assert changed == 1
    bonus = (await session.execute(
        select(Trader.achievement_bonus_percent).where(Trader.user_id == trader.id)
    )).scalar_one()
    assert bonus == Decimal("0.30")

    # breakdown: one row for the unified rule.
    breakdown = (await session.execute(
        select(TraderAchievement).where(TraderAchievement.trader_user_id == trader.id)
    )).scalars().all()
    assert {r.bonus_percent for r in breakdown} == {Decimal("0.30")}

    # cabinet view: one tier bar (unlocked) + one streak ring (active).
    view = await svc.get_trader_view(trader.id, today=TODAY)
    assert view["enabled"] is True
    assert view["total_bonus_percent"] == pytest.approx(0.3)

    assert len(view["tiers"]) == 1
    tier = view["tiers"][0]
    assert tier["window_days"] == 3
    assert tier["avg_volume_usdt"] == pytest.approx(600.0)
    assert tier["current_index"] == 1
    assert tier["current_percent"] == pytest.approx(0.3)
    assert tier["locked"] is False
    assert [lvl["threshold_usdt"] for lvl in tier["levels"]] == [pytest.approx(100.0), pytest.approx(500.0)]

    assert len(view["streaks"]) == 1
    streak = view["streaks"][0]
    assert streak["current_days"] == 3
    assert streak["target_days"] == 3
    assert streak["active"] is True
    assert streak["min_volume_usdt"] == pytest.approx(100.0)
    assert streak["bonus_percent"] == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_streak_not_reached_locks_bonus(session):
    """Only 2 qualifying days (need 3) → no bonus, and the view shows the tier
    LOCKED with the streak not active."""
    _enable(session)
    trader = await _mk_trader(session)
    merchant = await _mk_merchant(session)
    for offset in (0, 1):  # only 2 days
        await _mk_order(session, merchant_id=merchant.id, trader_user_id=trader.id,
                        day=TODAY - timedelta(days=offset), amount_usdt=Decimal("600"))
    await session.flush()

    svc = AchievementService(session)
    for offset in (0, 1):
        await svc.refresh_volume_for_day(TODAY - timedelta(days=offset))
    await svc.recompute_bonuses(today=TODAY)
    await session.flush()

    bonus = (await session.execute(
        select(Trader.achievement_bonus_percent).where(Trader.user_id == trader.id)
    )).scalar_one()
    assert bonus == Decimal("0")

    view = await svc.get_trader_view(trader.id, today=TODAY)
    assert view["streaks"][0]["active"] is False
    assert view["streaks"][0]["current_days"] == 2
    assert view["tiers"][0]["locked"] is True


@pytest.mark.asyncio
async def test_settings_json_rules_roundtrip(session):
    """The admin config path (PATCH /platform-settings/achievements) writes the
    rules as a JSON platform setting — verify SettingsService set/get round-trips
    the list the achievements job later reads."""
    svc = SettingsService(session)
    await svc.set("achievements_enabled", True)
    await svc.set("achievement_rules", RULES)
    await session.flush()
    assert await svc.get_bool("achievements_enabled") is True
    assert await svc.get_json("achievement_rules") == RULES


@pytest.mark.asyncio
async def test_disabling_resets_bonus_to_zero(session):
    _enable(session)
    trader = await _mk_trader(session)
    merchant = await _mk_merchant(session)
    for offset in (0, 1, 2):
        await _mk_order(session, merchant_id=merchant.id, trader_user_id=trader.id,
                        day=TODAY - timedelta(days=offset), amount_usdt=Decimal("600"))
    await session.flush()

    svc = AchievementService(session)
    for offset in (0, 1, 2):
        await svc.refresh_volume_for_day(TODAY - timedelta(days=offset))
    await svc.recompute_bonuses(today=TODAY)
    await session.flush()
    bonus1 = (await session.execute(
        select(Trader.achievement_bonus_percent).where(Trader.user_id == trader.id)
    )).scalar_one()
    assert bonus1 == Decimal("0.30")

    # Turn the feature off → recompute clears the stale bonus.
    setting = (await session.execute(
        select(PlatformSetting).where(PlatformSetting.key == "achievements_enabled")
    )).scalar_one()
    setting.value = "false"
    await session.flush()
    await svc.recompute_bonuses(today=TODAY)
    await session.flush()
    bonus2 = (await session.execute(
        select(Trader.achievement_bonus_percent).where(Trader.user_id == trader.id)
    )).scalar_one()
    assert bonus2 == Decimal("0")
