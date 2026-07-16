"""Per-entity circuit breaker, Redis-backed sorted-set sliding window.

Mirrors the pattern used in app.modules.cascading.circuit_breaker but
parameterised by selector namespace so different selectors don't share state.

Keys:
  {ns}:cb:{entity_id}:requests   — zset of all attempts, score=ts
  {ns}:cb:{entity_id}:failures   — zset of failed attempts only
  {ns}:cb:{entity_id}:open       — set with a TTL while the breaker is OPEN
"""
from __future__ import annotations

import logging
import time
from typing import Optional
from uuid import uuid4

import redis.asyncio as redis

from app.modules.selector.config.models import CircuitBreakerConfig


logger = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(
        self,
        client: redis.Redis,
        namespace: str,
        config: CircuitBreakerConfig,
    ):
        self._client = client
        self._namespace = namespace.rstrip(":")
        self._config = config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    # --- key helpers --------------------------------------------------------

    def _requests_key(self, entity_id: str) -> str:
        return f"{self._namespace}:cb:{entity_id}:requests"

    def _failures_key(self, entity_id: str) -> str:
        return f"{self._namespace}:cb:{entity_id}:failures"

    def _open_key(self, entity_id: str) -> str:
        return f"{self._namespace}:cb:{entity_id}:open"

    # --- read path ----------------------------------------------------------

    async def is_open(self, entity_id: str) -> bool:
        if not self._config.enabled:
            return False
        try:
            return bool(await self._client.exists(self._open_key(entity_id)))
        except Exception as exc:
            logger.warning(
                "selector_cb_check_failed",
                extra={"entity_id": entity_id, "error": str(exc)},
            )
            return False

    async def filter_open(self, entity_ids: list[str]) -> set[str]:
        """Return the subset that's currently OPEN. Empty set if CB disabled."""
        if not self._config.enabled or not entity_ids:
            return set()
        try:
            pipe = self._client.pipeline()
            for eid in entity_ids:
                pipe.exists(self._open_key(eid))
            results = await pipe.execute()
        except Exception as exc:
            logger.warning("selector_cb_filter_failed", extra={"error": str(exc)})
            return set()
        return {eid for eid, present in zip(entity_ids, results) if present}

    # --- write paths --------------------------------------------------------

    async def record(self, entity_id: str, *, success: bool) -> None:
        if not self._config.enabled:
            return
        now = time.time()
        cutoff = now - self._config.window_sec
        member = f"{now}:{uuid4().hex}"
        try:
            pipe = self._client.pipeline()
            pipe.zremrangebyscore(self._requests_key(entity_id), 0, cutoff)
            pipe.zadd(self._requests_key(entity_id), {member: now})
            pipe.expire(self._requests_key(entity_id), self._config.window_sec * 2)
            if not success:
                pipe.zremrangebyscore(self._failures_key(entity_id), 0, cutoff)
                pipe.zadd(self._failures_key(entity_id), {member: now})
                pipe.expire(self._failures_key(entity_id), self._config.window_sec * 2)
            await pipe.execute()
        except Exception as exc:
            logger.warning(
                "selector_cb_record_failed",
                extra={"entity_id": entity_id, "error": str(exc)},
            )
            return

        if not success:
            await self._maybe_trip(entity_id, now)

    async def _maybe_trip(self, entity_id: str, now: float) -> None:
        cutoff = now - self._config.window_sec
        try:
            failures = await self._client.zcount(
                self._failures_key(entity_id), cutoff, "+inf"
            )
        except Exception as exc:
            logger.warning(
                "selector_cb_count_failed",
                extra={"entity_id": entity_id, "error": str(exc)},
            )
            return
        if failures < self._config.failure_threshold:
            return
        try:
            await self._client.set(
                self._open_key(entity_id), "1", ex=self._config.cooldown_sec
            )
        except Exception as exc:
            logger.warning(
                "selector_cb_trip_failed",
                extra={"entity_id": entity_id, "error": str(exc)},
            )
            return
        logger.warning(
            "selector_cb_tripped",
            extra={
                "entity_id": entity_id,
                "failures": failures,
                "cooldown_sec": self._config.cooldown_sec,
            },
        )

    async def state(self, entity_id: str) -> Optional[dict]:
        if not self._config.enabled:
            return None
        now = time.time()
        cutoff = now - self._config.window_sec
        try:
            requests = await self._client.zcount(
                self._requests_key(entity_id), cutoff, "+inf"
            )
            failures = await self._client.zcount(
                self._failures_key(entity_id), cutoff, "+inf"
            )
            open_ = await self._client.exists(self._open_key(entity_id))
            ttl = await self._client.ttl(self._open_key(entity_id))
        except Exception:
            return None
        return {
            "requests": int(requests),
            "failures": int(failures),
            "open": bool(open_),
            "cooldown_remaining_sec": int(ttl) if ttl and ttl > 0 else 0,
        }
