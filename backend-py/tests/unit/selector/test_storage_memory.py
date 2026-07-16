import time

import pytest

from app.modules.selector import EntityStats, MemoryStorage, MetricValue, new_stats


@pytest.mark.asyncio
async def test_save_and_get_roundtrip(memory_storage: MemoryStorage):
    s = new_stats("e1")
    s.metrics["x"] = MetricValue(value=0.5, sample_size=10)
    await memory_storage.save(s)
    loaded = await memory_storage.get("e1")
    assert loaded is not None
    assert loaded.entity_id == "e1"
    assert loaded.metrics["x"].value == 0.5


@pytest.mark.asyncio
async def test_get_missing_returns_none(memory_storage: MemoryStorage):
    assert await memory_storage.get("nope") is None


@pytest.mark.asyncio
async def test_get_many_skips_missing(memory_storage: MemoryStorage):
    await memory_storage.save(new_stats("a"))
    await memory_storage.save(new_stats("b"))
    got = await memory_storage.get_many(["a", "b", "missing"])
    assert set(got.keys()) == {"a", "b"}


@pytest.mark.asyncio
async def test_apply_feedback_updates_alpha_beta(memory_storage: MemoryStorage):
    await memory_storage.save(new_stats("e", alpha=1.0, beta=1.0))
    updated = await memory_storage.apply_feedback(
        "e", reward=1.0, decay_factor=1.0, decay_interval_sec=60, now=time.time()
    )
    assert updated is not None
    assert updated.alpha == pytest.approx(2.0)
    assert updated.beta == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_apply_feedback_missing_returns_none(memory_storage: MemoryStorage):
    result = await memory_storage.apply_feedback(
        "nope", reward=1.0, decay_factor=1.0, decay_interval_sec=60, now=time.time()
    )
    assert result is None


@pytest.mark.asyncio
async def test_feedback_token_is_one_shot(memory_storage: MemoryStorage):
    assert await memory_storage.claim_feedback_token("o1", ttl_sec=60) is True
    assert await memory_storage.claim_feedback_token("o1", ttl_sec=60) is False
    assert await memory_storage.claim_feedback_token("o2", ttl_sec=60) is True


@pytest.mark.asyncio
async def test_get_returns_defensive_copy(memory_storage: MemoryStorage):
    """Caller mutations must not leak into stored state."""
    await memory_storage.save(new_stats("e", alpha=1.0))
    s1 = await memory_storage.get("e")
    s1.alpha = 999.0
    s2 = await memory_storage.get("e")
    assert s2.alpha == 1.0


@pytest.mark.asyncio
async def test_increment_selection_counter(memory_storage: MemoryStorage):
    await memory_storage.save(new_stats("e"))
    n = await memory_storage.increment_selection_counter("e")
    assert n == 1
    n = await memory_storage.increment_selection_counter("e")
    assert n == 2


def test_entity_stats_to_dict_from_dict_roundtrip():
    s = EntityStats(
        entity_id="e",
        metrics={"m": MetricValue(value=0.4, sample_size=5, updated_at=1234.0)},
        alpha=2.5,
        beta=1.5,
        total_selections=7,
        enabled=False,
        tags={"region": "ru"},
    )
    rebuilt = EntityStats.from_dict(s.to_dict())
    assert rebuilt == s
