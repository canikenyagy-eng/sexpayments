"""Storage protocol — what the selector engine asks of any backend.

Two implementations: ``MemoryStorage`` (process-local, for tests / cold starts /
graceful degradation) and ``RedisStorage`` (production).
"""
from __future__ import annotations

from typing import Iterable, Optional, Protocol

from app.modules.selector.core.stats import EntityStats


class SelectorStorage(Protocol):
    """All operations are async. Implementations must be safe under concurrency."""

    async def get(self, entity_id: str) -> Optional[EntityStats]: ...

    async def get_many(self, entity_ids: Iterable[str]) -> dict[str, EntityStats]: ...

    async def save(self, stats: EntityStats) -> None: ...

    async def apply_feedback(
        self,
        entity_id: str,
        reward: float,
        *,
        decay_factor: float,
        decay_interval_sec: int,
        now: float,
    ) -> Optional[EntityStats]:
        """Atomic decay + bandit update. Returns the new EntityStats or None if absent."""
        ...

    async def claim_feedback_token(self, order_id: str, ttl_sec: int) -> bool:
        """Idempotency check: returns True if this is the first feedback for ``order_id``."""
        ...

    async def increment_selection_counter(self, entity_id: str) -> int: ...

    async def list_entity_ids(self) -> list[str]: ...
