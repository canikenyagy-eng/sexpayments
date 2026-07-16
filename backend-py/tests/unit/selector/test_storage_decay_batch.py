"""decay_and_get_many — Lua-side batched decay + read."""
from __future__ import annotations

import time

import fakeredis.aioredis
import pytest

from app.modules.selector import MetricValue, new_stats
from app.modules.selector.storage.redis_storage import RedisStorage


@pytest.fixture
def storage():
    return RedisStorage(
        fakeredis.aioredis.FakeRedis(decode_responses=True),
        namespace="sel:test",
    )


@pytest.mark.asyncio
async def test_decay_and_get_many_empty_input(storage):
    assert (
        await storage.decay_and_get_many(
            [], decay_factor=0.99, decay_interval_sec=60, now=time.time()
        )
        == {}
    )


@pytest.mark.asyncio
async def test_decay_and_get_many_returns_decayed_stats(storage):
    await storage.save(new_stats("a", alpha=5.0, beta=3.0))
    s = await storage.get("a")
    base = s.last_updated

    # 60s later, gamma=0.5 → alpha 5 → 1 + 4*0.5 = 3, beta 3 → 1 + 2*0.5 = 2
    got = await storage.decay_and_get_many(
        ["a"], decay_factor=0.5, decay_interval_sec=60, now=base + 60.0
    )
    assert "a" in got
    assert got["a"].alpha == pytest.approx(3.0)
    assert got["a"].beta == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_decay_and_get_many_skips_missing(storage):
    await storage.save(new_stats("a", alpha=2.0))
    got = await storage.decay_and_get_many(
        ["a", "missing", "absent"],
        decay_factor=1.0,
        decay_interval_sec=60,
        now=time.time(),
    )
    assert set(got.keys()) == {"a"}


@pytest.mark.asyncio
async def test_decay_and_get_many_writes_back_decayed_value(storage):
    await storage.save(new_stats("a", alpha=5.0, beta=3.0))
    base = (await storage.get("a")).last_updated
    await storage.decay_and_get_many(
        ["a"], decay_factor=0.5, decay_interval_sec=60, now=base + 60.0
    )
    # A follow-up read should see the already-decayed value.
    s2 = await storage.get("a")
    assert s2.alpha == pytest.approx(3.0)
    assert s2.beta == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_decay_and_get_many_fallback_on_lua_error():
    """If the Lua script errors, fall back to plain get_many — no data loss."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    storage = RedisStorage(client, namespace="sel:test")
    await storage.save(new_stats("a", alpha=4.0))

    async def _boom(*a, **kw):
        raise RuntimeError("lua broken")

    storage._decay_batch_script = _boom  # type: ignore[assignment]
    got = await storage.decay_and_get_many(
        ["a"], decay_factor=0.5, decay_interval_sec=60, now=time.time()
    )
    # Fell back to get_many — stats present but un-decayed.
    assert got["a"].alpha == 4.0


@pytest.mark.asyncio
async def test_decay_and_get_many_handles_corrupt_entries(storage):
    """A corrupt blob in one slot shouldn't fail the whole batch."""
    await storage.save(new_stats("good", alpha=2.0))
    await storage._client.set("sel:test:stats:bad", "{not-json")
    got = await storage.decay_and_get_many(
        ["good", "bad"],
        decay_factor=1.0,
        decay_interval_sec=60,
        now=time.time(),
    )
    assert "good" in got
    assert "bad" not in got
