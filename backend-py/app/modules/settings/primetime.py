"""Prime-Time: a temporary, platform-wide boost to every trader's payin fee.

An admin activates a ``+X`` percentage-point bump for N minutes. While active,
order creation adds X points to the trader's per-method fee — the boosted value
is locked onto the order at creation (``order.trader_fee_usdt``), so the deal
keeps the boost even if it completes after the window ends.

State is a single Redis key with TTL = N minutes (auto-expiry, no cron). The
hot-path accessor ``get_active_points()`` is wrapped in a tiny process-local
cache (~3s) so order creation does at most one Redis GET per few seconds per
worker — the boost never adds per-order I/O. Any Redis failure degrades to
"no boost": prime-time can never slow or block order creation.
"""
import json
import time
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.types import utcnow
from app.core.exceptions import ValidationException
from app.core.logging import get_logger
from app.infrastructure.cache.redis import redis_client
from app.modules.base.service import BaseService

logger = get_logger(__name__)

PRIMETIME_KEY = "primetime:active"

MIN_POINTS = Decimal("0.01")
MAX_POINTS = Decimal("50")
MAX_MINUTES = 43200  # 30 days (a month)

# Hot-path micro-cache: order creation reads an in-memory value and refreshes
# from Redis at most once per this many seconds per worker.
_CACHE_TTL_S = 3.0
_cache: dict = {"points": Decimal("0"), "expires_at": 0.0}


class PrimeTimeState(BaseModel):
    points: Decimal
    ends_at: datetime


def _parse(raw: Optional[str]) -> Optional[PrimeTimeState]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return PrimeTimeState(
            points=Decimal(str(data["points"])),
            ends_at=datetime.fromisoformat(data["ends_at"]),
        )
    except Exception as exc:  # malformed value → treat as inactive
        logger.warning("primetime parse failed: %s", exc)
        return None


async def get_state() -> Optional[PrimeTimeState]:
    """Current window or ``None``. Reads Redis directly — used by the banner
    endpoint, NOT the order hot path."""
    return _parse(await redis_client.get(PRIMETIME_KEY))


async def get_active_points() -> Decimal:
    """Hot-path accessor: current boost in percentage points (0 if inactive).

    Process-local micro-cache (~3s) over Redis so order creation adds ~no I/O.
    Any failure → ``0`` (fail-safe; must never break/slow order creation).
    """
    now = time.monotonic()
    if now < _cache["expires_at"]:
        return _cache["points"]

    points = Decimal("0")
    try:
        state = _parse(await redis_client.get(PRIMETIME_KEY))
        if state and state.points > 0:
            points = state.points
    except Exception as exc:  # noqa: BLE001 — must never break order creation
        logger.warning("primetime read failed, treating as inactive: %s", exc)
        points = Decimal("0")

    _cache["points"] = points
    _cache["expires_at"] = now + _CACHE_TTL_S
    return points


def _invalidate_local_cache() -> None:
    _cache["expires_at"] = 0.0


def _validate(points: Decimal, minutes: int) -> None:
    if points < MIN_POINTS or points > MAX_POINTS:
        raise ValidationException(f"points must be in [{MIN_POINTS}, {MAX_POINTS}]")
    if minutes <= 0 or minutes > MAX_MINUTES:
        raise ValidationException(f"minutes must be in [1, {MAX_MINUTES}]")


async def set_window(points: Decimal, minutes: int) -> PrimeTimeState:
    """Write/replace the Redis window with TTL = ``minutes``. Returns the state."""
    _validate(points, minutes)
    ends_at = utcnow() + timedelta(minutes=minutes)
    payload = json.dumps({"points": str(points), "ends_at": ends_at.isoformat()})
    await redis_client.set(PRIMETIME_KEY, payload, ex=minutes * 60)
    _invalidate_local_cache()
    return PrimeTimeState(points=points, ends_at=ends_at)


async def clear_window() -> None:
    await redis_client.delete(PRIMETIME_KEY)
    _invalidate_local_cache()


class PrimeTimeService(BaseService):
    """Admin operations (activate / stop) — Redis write + audit trail."""

    def __init__(self, session: AsyncSession):
        super().__init__(session)

    async def activate(self, points: Decimal, minutes: int, admin_user_id: int) -> PrimeTimeState:
        state = await set_window(points, minutes)
        async with self.session.begin_nested():
            await self.audit_log(
                action="primetime_activate",
                entity_type="platform_setting",
                entity_id="primetime",
                user_id=admin_user_id,
                new_values={
                    "points": str(points),
                    "minutes": minutes,
                    "ends_at": state.ends_at.isoformat(),
                },
            )
        return state

    async def stop(self, admin_user_id: int) -> None:
        await clear_window()
        async with self.session.begin_nested():
            await self.audit_log(
                action="primetime_stop",
                entity_type="platform_setting",
                entity_id="primetime",
                user_id=admin_user_id,
            )
