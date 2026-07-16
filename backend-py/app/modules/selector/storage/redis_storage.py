"""Redis-backed storage. Atomic state updates go through Lua so concurrent
feedback() calls can't race-condition the α/β counters.

The EntityStats blob is stored as one JSON string per entity at
``{namespace}:stats:{entity_id}``. We keep it that way (instead of a Hash)
because Lua's cjson can decode/encode JSON in one shot, which keeps the
update script short and atomic.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable, Optional

import redis.asyncio as redis

from app.modules.selector.core.stats import EntityStats
from app.modules.selector.storage.protocol import SelectorStorage  # noqa: F401  (re-export shape)


logger = logging.getLogger(__name__)


_LUA_DIR = Path(__file__).parent / "lua"


def _read_script(name: str) -> str:
    return (_LUA_DIR / name).read_text(encoding="utf-8")


class RedisStorage:
    def __init__(
        self,
        client: redis.Redis,
        namespace: str,
        feedback_ttl_sec: int = 86_400,
    ):
        self._client = client
        self._namespace = namespace.rstrip(":")
        self._feedback_ttl = feedback_ttl_sec

        self._update_script = client.register_script(_read_script("update_bandit.lua"))
        self._incr_script = client.register_script(_read_script("increment_selection.lua"))
        self._decay_batch_script = client.register_script(
            _read_script("decay_and_get_batch.lua")
        )

    # --- key helpers --------------------------------------------------------

    def _stats_key(self, entity_id: str) -> str:
        return f"{self._namespace}:stats:{entity_id}"

    def _feedback_key(self, order_id: str) -> str:
        return f"{self._namespace}:fb:{order_id}"

    # --- read paths ---------------------------------------------------------

    async def get(self, entity_id: str) -> Optional[EntityStats]:
        raw = await self._client.get(self._stats_key(entity_id))
        if not raw:
            return None
        return EntityStats.from_dict(json.loads(raw))

    async def get_many(self, entity_ids: Iterable[str]) -> dict[str, EntityStats]:
        ids = list(entity_ids)
        if not ids:
            return {}
        keys = [self._stats_key(eid) for eid in ids]
        raws = await self._client.mget(keys)
        out: dict[str, EntityStats] = {}
        for eid, raw in zip(ids, raws):
            if not raw:
                continue
            try:
                out[eid] = EntityStats.from_dict(json.loads(raw))
            except (ValueError, KeyError) as exc:
                logger.warning(
                    "selector_stats_decode_failed", extra={"entity_id": eid, "error": str(exc)}
                )
        return out

    async def decay_and_get_many(
        self,
        entity_ids: Iterable[str],
        *,
        decay_factor: float,
        decay_interval_sec: int,
        now: float,
    ) -> dict[str, EntityStats]:
        """Server-side decay + batched read in one round-trip.

        Useful for high-fanout select() where applying decay client-side
        would mean N writes after the MGET. Falls back to plain ``get_many``
        on any Lua error so callers don't lose data on a Redis hiccup.
        """
        ids = list(entity_ids)
        if not ids:
            return {}
        keys = [self._stats_key(eid) for eid in ids]
        try:
            results = await self._decay_batch_script(
                keys=keys, args=[decay_factor, decay_interval_sec, now]
            )
        except Exception as exc:
            logger.warning("selector_decay_batch_failed: %s", exc)
            return await self.get_many(ids)
        out: dict[str, EntityStats] = {}
        for eid, raw in zip(ids, results):
            if not raw:
                continue
            raw_str = raw.decode() if isinstance(raw, bytes) else raw
            try:
                out[eid] = EntityStats.from_dict(json.loads(raw_str))
            except (ValueError, KeyError) as exc:
                logger.warning(
                    "selector_stats_decode_failed", extra={"entity_id": eid, "error": str(exc)}
                )
        return out

    async def list_entity_ids(self) -> list[str]:
        pattern = f"{self._namespace}:stats:*"
        prefix = f"{self._namespace}:stats:"
        ids: list[str] = []
        async for key in self._client.scan_iter(match=pattern, count=500):
            key_str = key.decode() if isinstance(key, bytes) else key
            if key_str.startswith(prefix):
                ids.append(key_str[len(prefix):])
        return ids

    # --- write paths --------------------------------------------------------

    async def save(self, stats: EntityStats) -> None:
        payload = json.dumps(stats.to_dict())
        await self._client.set(self._stats_key(stats.entity_id), payload)

    async def apply_feedback(
        self,
        entity_id: str,
        reward: float,
        *,
        decay_factor: float,
        decay_interval_sec: int,
        now: float,
    ) -> Optional[EntityStats]:
        reward = max(0.0, min(1.0, reward))
        result = await self._update_script(
            keys=[self._stats_key(entity_id)],
            args=[reward, decay_factor, decay_interval_sec, now],
        )
        if not result:
            return None
        raw = result.decode() if isinstance(result, bytes) else result
        return EntityStats.from_dict(json.loads(raw))

    async def claim_feedback_token(self, order_id: str, ttl_sec: int) -> bool:
        ok = await self._client.set(
            self._feedback_key(order_id), "1", ex=ttl_sec, nx=True
        )
        return bool(ok)

    async def increment_selection_counter(self, entity_id: str) -> int:
        result = await self._incr_script(keys=[self._stats_key(entity_id)])
        if result is None:
            return 0
        return int(result)
