"""RedisStorage tests against fakeredis (with Lua support via lupa).

Covers serialization round-trip, batch reads, the atomic Lua-backed feedback
update, idempotency tokens, and the SCAN-based entity listing.
"""
from __future__ import annotations

import time

import fakeredis.aioredis
import pytest

from app.modules.selector import MetricValue, new_stats
from app.modules.selector.storage.redis_storage import RedisStorage


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.fixture
def storage(fake_redis):
    return RedisStorage(fake_redis, namespace="sel:test")


@pytest.mark.asyncio
async def test_save_and_get_roundtrip(storage):
    s = new_stats("e1")
    s.metrics["conversion"] = MetricValue(value=0.7, sample_size=42)
    await storage.save(s)
    loaded = await storage.get("e1")
    assert loaded is not None
    assert loaded.entity_id == "e1"
    assert loaded.metrics["conversion"].value == pytest.approx(0.7)
    assert loaded.metrics["conversion"].sample_size == 42


@pytest.mark.asyncio
async def test_get_missing_returns_none(storage):
    assert await storage.get("nope") is None


@pytest.mark.asyncio
async def test_get_many_batches(storage):
    for eid in ["a", "b", "c"]:
        await storage.save(new_stats(eid))
    got = await storage.get_many(["a", "b", "missing", "c"])
    assert set(got.keys()) == {"a", "b", "c"}


@pytest.mark.asyncio
async def test_get_many_empty_input(storage):
    assert await storage.get_many([]) == {}


@pytest.mark.asyncio
async def test_get_many_skips_corrupt_entries(storage, fake_redis):
    await storage.save(new_stats("good"))
    await fake_redis.set("sel:test:stats:bad", "{not-json")
    got = await storage.get_many(["good", "bad"])
    assert "good" in got
    assert "bad" not in got


@pytest.mark.asyncio
async def test_apply_feedback_atomic(storage):
    await storage.save(new_stats("e", alpha=1.0, beta=1.0))
    updated = await storage.apply_feedback(
        "e", reward=1.0, decay_factor=1.0, decay_interval_sec=60, now=time.time()
    )
    assert updated is not None
    assert updated.alpha == pytest.approx(2.0)
    assert updated.beta == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_apply_feedback_with_decay(storage):
    """Lua should apply decay before adding the reward."""
    await storage.save(
        new_stats("e", alpha=5.0, beta=3.0)
    )
    s = await storage.get("e")
    base = s.last_updated
    # 60s later with decay_factor=0.5 → alpha goes 5 -> 1 + 4*0.5 = 3, then +1 (reward) -> 4
    updated = await storage.apply_feedback(
        "e", reward=1.0, decay_factor=0.5, decay_interval_sec=60, now=base + 60.0
    )
    assert updated is not None
    assert updated.alpha == pytest.approx(4.0)
    assert updated.beta == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_apply_feedback_missing_returns_none(storage):
    result = await storage.apply_feedback(
        "missing",
        reward=1.0,
        decay_factor=1.0,
        decay_interval_sec=60,
        now=time.time(),
    )
    assert result is None


@pytest.mark.asyncio
async def test_apply_feedback_clamps_reward(storage):
    await storage.save(new_stats("e", alpha=1.0, beta=1.0))
    over = await storage.apply_feedback(
        "e", reward=99.0, decay_factor=1.0, decay_interval_sec=60, now=time.time()
    )
    assert over.alpha == pytest.approx(2.0)
    await storage.save(new_stats("e2", alpha=1.0, beta=1.0))
    under = await storage.apply_feedback(
        "e2", reward=-99.0, decay_factor=1.0, decay_interval_sec=60, now=time.time()
    )
    assert under.beta == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_feedback_token_idempotent(storage):
    assert await storage.claim_feedback_token("o1", ttl_sec=60) is True
    assert await storage.claim_feedback_token("o1", ttl_sec=60) is False


@pytest.mark.asyncio
async def test_increment_selection_counter(storage):
    await storage.save(new_stats("e"))
    assert await storage.increment_selection_counter("e") == 1
    assert await storage.increment_selection_counter("e") == 2
    assert await storage.increment_selection_counter("missing") == 0


@pytest.mark.asyncio
async def test_list_entity_ids_via_scan(storage):
    for eid in ["a", "b", "c", "d"]:
        await storage.save(new_stats(eid))
    ids = await storage.list_entity_ids()
    assert set(ids) == {"a", "b", "c", "d"}


@pytest.mark.asyncio
async def test_namespace_isolation(fake_redis):
    a = RedisStorage(fake_redis, namespace="sel:a")
    b = RedisStorage(fake_redis, namespace="sel:b")
    await a.save(new_stats("e", alpha=5.0))
    await b.save(new_stats("e", alpha=10.0))
    sa = await a.get("e")
    sb = await b.get("e")
    assert sa.alpha == 5.0
    assert sb.alpha == 10.0


@pytest.mark.asyncio
async def test_namespace_strips_trailing_colon(fake_redis):
    """Constructor should accept ns with or without trailing colon."""
    s1 = RedisStorage(fake_redis, namespace="sel:test:")
    s2 = RedisStorage(fake_redis, namespace="sel:test")
    await s1.save(new_stats("e", alpha=7.0))
    loaded = await s2.get("e")
    assert loaded.alpha == 7.0
