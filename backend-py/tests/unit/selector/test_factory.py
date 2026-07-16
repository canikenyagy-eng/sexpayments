"""Factory dispatch and wiring."""
from __future__ import annotations

import fakeredis.aioredis
import pytest

from app.modules.selector import (
    EntitySelector,
    MemoryStorage,
    SelectorConfig,
    build_memory_selector,
    build_selector,
)
from app.modules.selector.config.models import CircuitBreakerConfig
from app.modules.selector.hooks.circuit_breaker import CircuitBreaker


def test_build_selector_requires_storage_or_redis():
    cfg = SelectorConfig(name="t", namespace="ns:t")
    with pytest.raises(ValueError, match="storage or redis_client"):
        build_selector(cfg)


def test_build_selector_with_explicit_storage():
    cfg = SelectorConfig(name="t", namespace="ns:t")
    sel = build_selector(cfg, storage=MemoryStorage())
    assert isinstance(sel, EntitySelector)
    assert sel._cb is None  # no Redis client → no CB even if config enabled


def test_build_selector_with_redis_attaches_storage_but_no_cb_when_disabled():
    cfg = SelectorConfig(name="t", namespace="ns:t")
    sel = build_selector(cfg, redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True))
    assert isinstance(sel, EntitySelector)
    assert sel._cb is None  # CB disabled in config


def test_build_selector_with_redis_attaches_cb_when_enabled():
    cfg = SelectorConfig(
        name="t",
        namespace="ns:t",
        circuit_breaker=CircuitBreakerConfig(enabled=True),
    )
    sel = build_selector(
        cfg, redis_client=fakeredis.aioredis.FakeRedis(decode_responses=True)
    )
    assert isinstance(sel._cb, CircuitBreaker)
    assert sel._cb.enabled is True


def test_build_selector_cb_not_attached_without_redis_even_if_enabled():
    """No Redis means no CB even when cfg says enabled — fail gracefully."""
    cfg = SelectorConfig(
        name="t",
        namespace="ns:t",
        circuit_breaker=CircuitBreakerConfig(enabled=True),
    )
    sel = build_selector(cfg, storage=MemoryStorage())
    assert sel._cb is None


def test_build_memory_selector_convenience():
    cfg = SelectorConfig(name="t", namespace="ns:t")
    sel = build_memory_selector(cfg)
    assert isinstance(sel, EntitySelector)
    assert isinstance(sel._storage, MemoryStorage)
    assert sel._cb is None
