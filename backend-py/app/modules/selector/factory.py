"""Factory: turn a SelectorConfig + storage backend into a wired EntitySelector."""
from __future__ import annotations

import random
from typing import Optional

import redis.asyncio as redis

from app.modules.selector.config.models import SelectorConfig
from app.modules.selector.core.selector import EntitySelector
from app.modules.selector.experiments.ab import ABRouter, RedisStickyStore
from app.modules.selector.hooks.circuit_breaker import CircuitBreaker
from app.modules.selector.logging_.sinks import EventSink
from app.modules.selector.storage.memory_storage import MemoryStorage
from app.modules.selector.storage.protocol import SelectorStorage
from app.modules.selector.storage.redis_storage import RedisStorage


def build_selector(
    config: SelectorConfig,
    *,
    storage: Optional[SelectorStorage] = None,
    redis_client: Optional[redis.Redis] = None,
    event_sink: Optional[EventSink] = None,
    rng: Optional[random.Random] = None,
) -> EntitySelector:
    """Build a fully-wired EntitySelector.

    Provide ``storage`` directly (e.g. MemoryStorage for tests), or pass
    ``redis_client`` to get the RedisStorage path. Exactly one must be set.
    """
    if storage is None:
        if redis_client is None:
            raise ValueError("either storage or redis_client must be provided")
        storage = RedisStorage(redis_client, config.namespace)

    cb: Optional[CircuitBreaker] = None
    if config.circuit_breaker.enabled and redis_client is not None:
        cb = CircuitBreaker(redis_client, config.namespace, config.circuit_breaker)

    ab: Optional[ABRouter] = None
    if config.experiments:
        sticky = RedisStickyStore(redis_client) if redis_client is not None else None
        ab = ABRouter(
            config.experiments,
            sticky=sticky,
            sticky_namespace=config.namespace,
        )

    return EntitySelector(
        config=config,
        storage=storage,
        circuit_breaker=cb,
        ab_router=ab,
        event_sink=event_sink,
        rng=rng,
    )


def build_memory_selector(
    config: SelectorConfig,
    *,
    rng: Optional[random.Random] = None,
    event_sink: Optional[EventSink] = None,
) -> EntitySelector:
    """Convenience for tests: build with MemoryStorage, no CB, no AB."""
    return EntitySelector(
        config=config,
        storage=MemoryStorage(),
        circuit_breaker=None,
        ab_router=None,
        event_sink=event_sink,
        rng=rng,
    )
