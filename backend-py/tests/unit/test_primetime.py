"""Unit tests for the Prime-Time module (Redis-backed global trader-fee boost).

Focus: the hot-path accessor `get_active_points` (cache + fail-safe), state
parsing, validation, and the admin service (Redis write + audit).
"""
import json
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import ValidationException
from app.modules.settings import primetime as pt


@pytest.fixture(autouse=True)
def _reset_cache():
    pt._cache["points"] = Decimal("0")
    pt._cache["expires_at"] = 0.0
    yield
    pt._cache["points"] = Decimal("0")
    pt._cache["expires_at"] = 0.0


def _payload(points: str = "1", minutes: int = 10) -> str:
    ends = datetime(2026, 1, 1) + timedelta(minutes=minutes)
    return json.dumps({"points": points, "ends_at": ends.isoformat()})


class _Ctx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _redis(value):
    fake = AsyncMock()
    fake.get = AsyncMock(return_value=value)
    return fake


# ── get_active_points (hot path) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_active_points_inactive_zero():
    fake = AsyncMock()
    fake.get = AsyncMock(return_value=None)
    with patch.object(pt, "redis_client", fake):
        assert await pt.get_active_points() == Decimal("0")


@pytest.mark.asyncio
async def test_active_points_returns_value():
    fake = AsyncMock()
    fake.get = AsyncMock(return_value=_payload(points="1.5"))
    with patch.object(pt, "redis_client", fake):
        assert await pt.get_active_points() == Decimal("1.5")


@pytest.mark.asyncio
async def test_active_points_cached_no_second_redis_call():
    """Hot-path guarantee: a second read inside the cache window does NOT hit Redis."""
    fake = AsyncMock()
    fake.get = AsyncMock(return_value=_payload(points="2"))
    with patch.object(pt, "redis_client", fake):
        v1 = await pt.get_active_points()
        v2 = await pt.get_active_points()
    assert v1 == v2 == Decimal("2")
    fake.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_active_points_redis_error_is_failsafe_zero():
    fake = AsyncMock()
    fake.get = AsyncMock(side_effect=RuntimeError("redis down"))
    with patch.object(pt, "redis_client", fake):
        assert await pt.get_active_points() == Decimal("0")


@pytest.mark.asyncio
async def test_active_points_negative_treated_inactive():
    fake = AsyncMock()
    fake.get = AsyncMock(return_value=_payload(points="-5"))
    with patch.object(pt, "redis_client", fake):
        assert await pt.get_active_points() == Decimal("0")


# ── get_state (banner) ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_state_none_when_absent():
    fake = AsyncMock()
    fake.get = AsyncMock(return_value=None)
    with patch.object(pt, "redis_client", fake):
        assert await pt.get_state() is None


@pytest.mark.asyncio
async def test_get_state_parses_value():
    fake = AsyncMock()
    fake.get = AsyncMock(return_value=_payload(points="3", minutes=20))
    with patch.object(pt, "redis_client", fake):
        state = await pt.get_state()
    assert state is not None
    assert state.points == Decimal("3")


@pytest.mark.asyncio
async def test_get_state_malformed_is_none():
    fake = AsyncMock()
    fake.get = AsyncMock(return_value="{not json")
    with patch.object(pt, "redis_client", fake):
        assert await pt.get_state() is None


# ── set_window / validation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_window_writes_redis_with_ttl():
    fake = AsyncMock()
    fake.set = AsyncMock()
    with patch.object(pt, "redis_client", fake):
        state = await pt.set_window(Decimal("1.5"), 10)
    assert state.points == Decimal("1.5")
    args, kwargs = fake.set.call_args
    assert args[0] == pt.PRIMETIME_KEY
    assert kwargs["ex"] == 600  # 10 min × 60
    assert json.loads(args[1])["points"] == "1.5"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "points,minutes",
    [
        (Decimal("0"), 10),
        (Decimal("-1"), 10),
        (Decimal("0.005"), 10),  # below MIN_POINTS (0.01% step)
        (Decimal("51"), 10),  # over MAX_POINTS
        (Decimal("1"), 0),
        (Decimal("1"), 43201),  # over MAX_MINUTES (30 days + 1)
    ],
)
async def test_set_window_validation_rejects(points, minutes):
    with pytest.raises(ValidationException):
        await pt.set_window(points, minutes)


@pytest.mark.asyncio
async def test_set_window_accepts_max_minutes_one_month():
    """The window may now span up to 43200 minutes (30 days)."""
    fake = AsyncMock()
    fake.set = AsyncMock()
    with patch.object(pt, "redis_client", fake):
        state = await pt.set_window(Decimal("1"), pt.MAX_MINUTES)
    assert state.points == Decimal("1")
    _, kwargs = fake.set.call_args
    assert kwargs["ex"] == pt.MAX_MINUTES * 60  # 43200 min × 60 = 30 days


# ── PrimeTimeService (admin: redis + audit) ───────────────────────────────


@pytest.mark.asyncio
async def test_service_activate_writes_and_audits():
    session = MagicMock()
    session.begin_nested = MagicMock(return_value=_Ctx())
    svc = pt.PrimeTimeService(session)
    svc.audit_log = AsyncMock()

    fake = AsyncMock()
    fake.set = AsyncMock()
    with patch.object(pt, "redis_client", fake):
        state = await svc.activate(Decimal("1"), 5, admin_user_id=7)

    assert state.points == Decimal("1")
    fake.set.assert_awaited_once()
    svc.audit_log.assert_awaited_once()
    assert svc.audit_log.call_args.kwargs["action"] == "primetime_activate"


@pytest.mark.asyncio
async def test_service_stop_deletes_and_audits():
    session = MagicMock()
    session.begin_nested = MagicMock(return_value=_Ctx())
    svc = pt.PrimeTimeService(session)
    svc.audit_log = AsyncMock()

    fake = AsyncMock()
    fake.delete = AsyncMock()
    with patch.object(pt, "redis_client", fake):
        await svc.stop(admin_user_id=7)

    fake.delete.assert_awaited_once_with(pt.PRIMETIME_KEY)
    svc.audit_log.assert_awaited_once()
    assert svc.audit_log.call_args.kwargs["action"] == "primetime_stop"


# ── The money behaviour: _calculate_trader_fee applies the boost ──────────


@pytest.mark.asyncio
async def test_calculate_trader_fee_applies_boost():
    """The actual integration: order creation adds +points to the trader fee.

    Trader fee = 2% for sbp. Without Prime-Time → 100 × 2% = 2.0000. With +1.5
    points → 100 × 3.5% = 3.5000. Locked onto the order at creation."""
    from app.modules.orders.service import OrderService

    mc = MagicMock()
    mc.payment_method = MagicMock()
    mc.payment_method.value = "sbp"
    mc.fee = 2.0
    trader = MagicMock()
    trader.method_configs = [mc]
    trader.achievement_bonus_percent = 0  # no achievements bonus in this scenario

    exec_result = MagicMock()
    exec_result.scalar_one_or_none = MagicMock(return_value=trader)
    session = MagicMock()
    session.execute = AsyncMock(return_value=exec_result)
    svc = OrderService(session)

    # No Prime-Time → base 2%.
    pt._cache["expires_at"] = 0.0
    with patch.object(pt, "redis_client", _redis(None)):
        base = await svc._calculate_trader_fee(5, "sbp", Decimal("100"))
    assert base == Decimal("2.0000")

    # Prime-Time +1.5 points → 3.5%.
    pt._cache["expires_at"] = 0.0
    with patch.object(pt, "redis_client", _redis(_payload(points="1.5"))):
        boosted = await svc._calculate_trader_fee(5, "sbp", Decimal("100"))
    assert boosted == Decimal("3.5000")

    # Hundredth-of-a-percent step: +0.01 points → 2.01%.
    pt._cache["expires_at"] = 0.0
    with patch.object(pt, "redis_client", _redis(_payload(points="0.01"))):
        fine = await svc._calculate_trader_fee(5, "sbp", Decimal("100"))
    assert fine == Decimal("2.0100")
