"""CircuitBreaker tests against fakeredis."""
from __future__ import annotations

import asyncio
import time

import fakeredis.aioredis
import pytest

from app.modules.selector.config.models import CircuitBreakerConfig
from app.modules.selector.hooks.circuit_breaker import CircuitBreaker


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _cb(fake_redis, **overrides):
    cfg = CircuitBreakerConfig(
        enabled=overrides.get("enabled", True),
        failure_threshold=overrides.get("failure_threshold", 3),
        window_sec=overrides.get("window_sec", 60),
        cooldown_sec=overrides.get("cooldown_sec", 120),
    )
    return CircuitBreaker(fake_redis, namespace="sel:test", config=cfg)


@pytest.mark.asyncio
async def test_disabled_cb_short_circuits(fake_redis):
    cb = _cb(fake_redis, enabled=False)
    assert cb.enabled is False
    # All operations are no-ops when disabled.
    assert await cb.is_open("any") is False
    assert await cb.filter_open(["a", "b"]) == set()
    await cb.record("a", success=False)  # must not raise
    assert await cb.state("any") is None


@pytest.mark.asyncio
async def test_is_open_default_closed(fake_redis):
    cb = _cb(fake_redis)
    assert await cb.is_open("fresh") is False


@pytest.mark.asyncio
async def test_trips_after_threshold_failures(fake_redis):
    cb = _cb(fake_redis, failure_threshold=3)
    await cb.record("e", success=False)
    await cb.record("e", success=False)
    assert await cb.is_open("e") is False
    await cb.record("e", success=False)
    assert await cb.is_open("e") is True


@pytest.mark.asyncio
async def test_success_does_not_trip(fake_redis):
    cb = _cb(fake_redis, failure_threshold=3)
    for _ in range(10):
        await cb.record("e", success=True)
    assert await cb.is_open("e") is False


@pytest.mark.asyncio
async def test_filter_open_returns_only_open_entities(fake_redis):
    cb = _cb(fake_redis, failure_threshold=2)
    # Trip 'broken'
    await cb.record("broken", success=False)
    await cb.record("broken", success=False)
    # Healthy entity has no failures.
    await cb.record("healthy", success=True)
    open_set = await cb.filter_open(["broken", "healthy", "untouched"])
    assert "broken" in open_set
    assert "healthy" not in open_set
    assert "untouched" not in open_set


@pytest.mark.asyncio
async def test_filter_open_empty_input(fake_redis):
    cb = _cb(fake_redis)
    assert await cb.filter_open([]) == set()


@pytest.mark.asyncio
async def test_state_shows_counts_and_open_flag(fake_redis):
    cb = _cb(fake_redis, failure_threshold=2)
    await cb.record("e", success=False)
    await cb.record("e", success=False)
    state = await cb.state("e")
    assert state is not None
    assert state["failures"] >= 2
    assert state["requests"] >= 2
    assert state["open"] is True
    assert state["cooldown_remaining_sec"] > 0


@pytest.mark.asyncio
async def test_state_none_when_disabled(fake_redis):
    cb = _cb(fake_redis, enabled=False)
    assert await cb.state("e") is None


@pytest.mark.asyncio
async def test_below_threshold_does_not_trip(fake_redis):
    cb = _cb(fake_redis, failure_threshold=5)
    for _ in range(4):
        await cb.record("e", success=False)
    assert await cb.is_open("e") is False
    state = await cb.state("e")
    assert state["open"] is False


@pytest.mark.asyncio
async def test_redis_error_in_is_open_returns_false(monkeypatch):
    """When Redis raises, is_open must fail closed-as-passable (return False).

    Rationale: we'd rather over-route than fail every request when the CB
    backend hiccups.
    """
    cfg = CircuitBreakerConfig(enabled=True, failure_threshold=3)

    class _ExplodingRedis:
        async def exists(self, *a, **kw):
            raise RuntimeError("boom")

    cb = CircuitBreaker(_ExplodingRedis(), namespace="sel:test", config=cfg)
    assert await cb.is_open("any") is False


@pytest.mark.asyncio
async def test_redis_error_in_record_does_not_raise(monkeypatch):
    cfg = CircuitBreakerConfig(enabled=True, failure_threshold=3)

    class _Pipe:
        def __getattr__(self, name):
            def _f(*a, **kw):
                return self
            return _f

        async def execute(self):
            raise RuntimeError("boom")

    class _ExplodingRedis:
        def pipeline(self):
            return _Pipe()

    cb = CircuitBreaker(_ExplodingRedis(), namespace="sel:test", config=cfg)
    # Must not propagate
    await cb.record("e", success=False)


@pytest.mark.asyncio
async def test_redis_error_in_filter_open_returns_empty():
    cfg = CircuitBreakerConfig(enabled=True, failure_threshold=3)

    class _Pipe:
        def exists(self, *a, **kw):
            return self

        async def execute(self):
            raise RuntimeError("boom")

    class _ExplodingRedis:
        def pipeline(self):
            return _Pipe()

    cb = CircuitBreaker(_ExplodingRedis(), namespace="sel:test", config=cfg)
    assert await cb.filter_open(["a", "b"]) == set()


@pytest.mark.asyncio
async def test_redis_error_in_state_returns_none():
    cfg = CircuitBreakerConfig(enabled=True, failure_threshold=3)

    class _ExplodingRedis:
        async def zcount(self, *a, **kw):
            raise RuntimeError("boom")

    cb = CircuitBreaker(_ExplodingRedis(), namespace="sel:test", config=cfg)
    assert await cb.state("e") is None


@pytest.mark.asyncio
async def test_namespace_isolation(fake_redis):
    a = CircuitBreaker(
        fake_redis,
        namespace="sel:a",
        config=CircuitBreakerConfig(enabled=True, failure_threshold=2),
    )
    b = CircuitBreaker(
        fake_redis,
        namespace="sel:b",
        config=CircuitBreakerConfig(enabled=True, failure_threshold=2),
    )
    await a.record("shared", success=False)
    await a.record("shared", success=False)
    assert await a.is_open("shared") is True
    assert await b.is_open("shared") is False


@pytest.mark.asyncio
async def test_same_tick_failures_are_counted_not_deduped(fake_redis):
    """Regression: the zset member used to be ``f"{now}"`` so two failures on
    the identical timestamp (same-tick burst / retried feedback) collapsed via
    ``zadd`` dedup — undercounting failures and never tripping. The member is
    now unique, so same-tick failures both count.
    """
    from unittest.mock import patch

    cb = _cb(fake_redis, failure_threshold=2)
    fixed = 1_000_000.0
    with patch(
        "app.modules.selector.hooks.circuit_breaker.time.time", return_value=fixed
    ):
        await cb.record("e", success=False)
        await cb.record("e", success=False)
        # Two distinct members at the same score → 2 failures ≥ threshold → open.
        assert await cb.is_open("e") is True
