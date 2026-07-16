"""In-memory storage. Used in unit tests, simulations, and as a graceful
fallback when Redis is down (per-instance state, will diverge across workers).
"""
from __future__ import annotations

import asyncio
import time
from typing import Iterable, Optional

from app.modules.selector.core.bandit import apply_decay as _apply_decay_arith
from app.modules.selector.core.stats import EntityStats
from app.modules.selector.config.models import SelectorConfig


class _FakeConfig:
    """Minimal duck-typed stand-in for SelectorConfig.

    apply_decay only reads decay_factor and decay_interval_sec — we build a
    throwaway object so MemoryStorage doesn't need to be handed the full
    SelectorConfig at construction time.
    """

    __slots__ = ("decay_factor", "decay_interval_sec")

    def __init__(self, decay_factor: float, decay_interval_sec: int):
        self.decay_factor = decay_factor
        self.decay_interval_sec = decay_interval_sec


class MemoryStorage:
    def __init__(self) -> None:
        self._stats: dict[str, EntityStats] = {}
        self._feedback_tokens: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def get(self, entity_id: str) -> Optional[EntityStats]:
        s = self._stats.get(entity_id)
        return _clone(s) if s else None

    async def get_many(self, entity_ids: Iterable[str]) -> dict[str, EntityStats]:
        return {eid: _clone(self._stats[eid]) for eid in entity_ids if eid in self._stats}

    async def save(self, stats: EntityStats) -> None:
        async with self._lock:
            self._stats[stats.entity_id] = _clone(stats)

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
        async with self._lock:
            stats = self._stats.get(entity_id)
            if stats is None:
                return None
            _apply_decay_arith(
                stats,
                _FakeConfig(decay_factor, decay_interval_sec),  # type: ignore[arg-type]
                now=now,
            )
            stats.alpha += reward
            stats.beta += 1.0 - reward
            stats.last_updated = now
            return _clone(stats)

    async def claim_feedback_token(self, order_id: str, ttl_sec: int) -> bool:
        now = time.time()
        async with self._lock:
            expired = [k for k, exp in self._feedback_tokens.items() if exp <= now]
            for k in expired:
                self._feedback_tokens.pop(k, None)
            if order_id in self._feedback_tokens:
                return False
            self._feedback_tokens[order_id] = now + ttl_sec
            return True

    async def increment_selection_counter(self, entity_id: str) -> int:
        async with self._lock:
            stats = self._stats.get(entity_id)
            if stats is None:
                return 0
            stats.total_selections += 1
            return stats.total_selections

    async def list_entity_ids(self) -> list[str]:
        return list(self._stats.keys())


def _clone(stats: EntityStats) -> EntityStats:
    """Defensive copy so callers can't mutate stored state."""
    return EntityStats.from_dict(stats.to_dict())
