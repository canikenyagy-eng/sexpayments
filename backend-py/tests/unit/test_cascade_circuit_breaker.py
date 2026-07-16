"""Unit tests for the cascade circuit breaker.

The breaker uses Redis as backing store. We mock the redis_client module-level
instance with an in-memory fake so tests stay self-contained.
"""
from __future__ import annotations

import os
import time
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

# Seed env vars before app.* imports (pydantic-settings reads eagerly).
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost:5432/unused")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-tests-must-be-long-enough!!")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")

import pytest


class _FakePipeline:
    def __init__(self, store):
        self.store = store
        self.ops = []

    def zadd(self, key, mapping):
        self.ops.append(("zadd", key, mapping))
        return self

    def zremrangebyscore(self, key, lo, hi):
        self.ops.append(("zrem", key, lo, hi))
        return self

    def expire(self, key, ttl):
        return self

    async def execute(self):
        for op in self.ops:
            if op[0] == "zadd":
                _, key, mapping = op
                self.store.setdefault(key, [])
                for member, score in mapping.items():
                    self.store[key].append((float(score), member))
            elif op[0] == "zrem":
                _, key, lo, hi = op
                if key in self.store:
                    lo_f = -float("inf") if lo == "-inf" else float(lo)
                    hi_f = float("inf") if hi == "+inf" else float(hi)
                    self.store[key] = [
                        (s, m) for s, m in self.store[key] if not (lo_f <= s <= hi_f)
                    ]
        return [1] * len(self.ops)


class _FakeRedis:
    def __init__(self):
        self.store: dict[str, list[tuple[float, str]]] = {}

    def pipeline(self):
        return _FakePipeline(self.store)

    async def zcount(self, key, lo, hi):
        items = self.store.get(key, [])
        lo_f = -float("inf") if lo == "-inf" else float(lo)
        hi_f = float("inf") if hi == "+inf" else float(hi)
        return sum(1 for s, _ in items if lo_f <= s <= hi_f)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = _FakeRedis()
    from app.modules.cascading import circuit_breaker as cb_module

    monkeypatch.setattr(cb_module, "redis_client", fake)
    return fake


def _provider(**overrides):
    p = MagicMock()
    p.id = 42
    p.code = "test_provider"
    p.cb_window_seconds = 60
    p.cb_threshold_failures = 3
    p.cb_threshold_rate = 0.5
    p.cb_cooldown_seconds = 120
    p.disabled_until = None
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


@pytest.mark.asyncio
async def test_breaker_starts_closed(fake_redis):
    from app.modules.cascading.circuit_breaker import CircuitBreaker

    session = MagicMock()
    session.add = MagicMock()

    breaker = CircuitBreaker(session)
    assert not await breaker.is_open(_provider())


@pytest.mark.asyncio
async def test_breaker_trips_on_threshold(fake_redis):
    from app.modules.cascading.circuit_breaker import CircuitBreaker

    session = MagicMock()
    session.add = MagicMock()

    breaker = CircuitBreaker(session)
    provider = _provider()

    # 3 failures + 0 successes → rate 1.0 ≥ threshold 0.5 and count ≥ 3 → trip.
    for i in range(3):
        await breaker.record_failure(provider, code=f"err-{i}")

    assert provider.disabled_until is not None
    assert await breaker.is_open(provider)


@pytest.mark.asyncio
async def test_breaker_does_not_trip_below_threshold(fake_redis):
    from app.modules.cascading.circuit_breaker import CircuitBreaker

    session = MagicMock()
    session.add = MagicMock()

    breaker = CircuitBreaker(session)
    provider = _provider()

    # 2 failures < threshold (3) → not tripped even though rate is 1.0.
    for i in range(2):
        await breaker.record_failure(provider, code=f"err-{i}")

    assert provider.disabled_until is None
    assert not await breaker.is_open(provider)


@pytest.mark.asyncio
async def test_breaker_does_not_trip_when_rate_below_threshold(fake_redis):
    from app.modules.cascading.circuit_breaker import CircuitBreaker

    session = MagicMock()
    breaker = CircuitBreaker(session)
    provider = _provider(cb_threshold_failures=3, cb_threshold_rate=0.7)

    # 3 failures + 7 successes → rate 0.3 < 0.7 → no trip.
    for i in range(7):
        await breaker.record_success(provider)
    for i in range(3):
        await breaker.record_failure(provider, code=f"err-{i}")

    assert provider.disabled_until is None


@pytest.mark.asyncio
async def test_breaker_self_clears_after_cooldown(fake_redis):
    from app.common.types import utcnow
    from app.modules.cascading.circuit_breaker import CircuitBreaker

    session = MagicMock()
    breaker = CircuitBreaker(session)
    provider = _provider()
    provider.disabled_until = utcnow() - timedelta(seconds=1)  # already expired

    assert not await breaker.is_open(provider)
    assert provider.disabled_until is None
