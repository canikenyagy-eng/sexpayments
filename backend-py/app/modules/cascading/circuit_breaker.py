"""Per-provider circuit breaker backed by Redis sorted sets.

We keep a sorted set of recent attempt outcomes (one entry per attempt, score=ts):
    cascade:cb:{provider_id}:requests   — every attempt
    cascade:cb:{provider_id}:failures   — only the failed ones

When a provider exceeds its configured failure threshold inside its window we
flip ``CascadeProvider.disabled_until = now + cooldown`` and skip it from
cascade routing until the cooldown expires.
"""
from __future__ import annotations

import time
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.types import utcnow
from app.core.logging import get_logger
from app.infrastructure.cache.redis import redis_client
from app.modules.cascading.models import CascadeProvider
from datetime import timedelta

logger = get_logger(__name__)


def _requests_key(provider_id: int) -> str:
    return f"cascade:cb:{provider_id}:requests"


def _failures_key(provider_id: int) -> str:
    return f"cascade:cb:{provider_id}:failures"


class CircuitBreaker:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def is_open(self, provider: CascadeProvider) -> bool:
        """True if the breaker is currently blocking calls to this provider."""
        if provider.disabled_until and provider.disabled_until > utcnow():
            return True
        # Self-clear once the cooldown elapses so admin doesn't have to.
        if provider.disabled_until and provider.disabled_until <= utcnow():
            provider.disabled_until = None
            self.session.add(provider)
        return False

    async def record_success(self, provider: CascadeProvider) -> None:
        await self._record_request(provider, success=True)

    async def record_failure(self, provider: CascadeProvider, *, code: str = "") -> None:
        await self._record_request(provider, success=False, code=code)
        await self._maybe_trip(provider)

    async def _record_request(
        self, provider: CascadeProvider, *, success: bool, code: str = ""
    ) -> None:
        now = time.time()
        cutoff = now - provider.cb_window_seconds
        member = f"{now}:{code or 'ok'}"

        try:
            pipe = redis_client.pipeline()
            pipe.zremrangebyscore(_requests_key(provider.id), 0, cutoff)
            pipe.zadd(_requests_key(provider.id), {member: now})
            pipe.expire(_requests_key(provider.id), provider.cb_window_seconds * 2)
            if not success:
                pipe.zremrangebyscore(_failures_key(provider.id), 0, cutoff)
                pipe.zadd(_failures_key(provider.id), {member: now})
                pipe.expire(_failures_key(provider.id), provider.cb_window_seconds * 2)
            await pipe.execute()
        except Exception as exc:
            # Redis hiccups should never block the cascade flow. We log and
            # let the request through — `is_open` will simply return False
            # next time. (Better available than fail-closed here.)
            logger.warning(
                "circuit_breaker_record_failed",
                provider_id=provider.id,
                error=str(exc),
            )

    async def _maybe_trip(self, provider: CascadeProvider) -> None:
        try:
            now = time.time()
            cutoff = now - provider.cb_window_seconds
            requests = await redis_client.zcount(
                _requests_key(provider.id), cutoff, "+inf"
            )
            failures = await redis_client.zcount(
                _failures_key(provider.id), cutoff, "+inf"
            )
        except Exception as exc:
            logger.warning(
                "circuit_breaker_check_failed",
                provider_id=provider.id,
                error=str(exc),
            )
            return

        if failures < provider.cb_threshold_failures:
            return

        rate = (failures / requests) if requests else 0.0
        if rate < provider.cb_threshold_rate:
            return

        # Trip: write disabled_until on the provider row and audit-log it via
        # the calling service (we just stamp the row here; service-level
        # audit happens in CascadingService).
        provider.disabled_until = utcnow() + timedelta(seconds=provider.cb_cooldown_seconds)
        self.session.add(provider)
        logger.warning(
            "circuit_breaker_tripped",
            provider_id=provider.id,
            provider_code=provider.code,
            failures=failures,
            requests=requests,
            rate=round(rate, 3),
            disabled_until=provider.disabled_until.isoformat(),
        )
