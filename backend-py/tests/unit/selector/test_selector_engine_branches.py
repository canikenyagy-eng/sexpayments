"""Extra engine branch coverage — paths the main engine test file doesn't hit."""
from __future__ import annotations

import random

import fakeredis.aioredis
import pytest

from app.modules.selector import (
    MetricValue,
    SelectionContext,
    SelectorConfig,
    build_selector,
    new_stats,
)
from app.modules.selector.config.models import (
    CircuitBreakerConfig,
    ColdStartConfig,
    FairnessConfig,
    MetricSpec,
)
from app.modules.selector.storage.memory_storage import MemoryStorage


def _cfg(**overrides):
    base = dict(
        name="t",
        namespace="sel:test",
        metrics=(
            MetricSpec(
                name="conversion",
                weight=1.0,
                min_value=0.0,
                max_value=1.0,
            ),
        ),
    )
    base.update(overrides)
    return SelectorConfig(**base)


@pytest.mark.asyncio
async def test_select_without_explicit_context_uses_defaults():
    """select(candidate_ids=...) without a context arg still works."""
    sel = build_selector(_cfg(), storage=MemoryStorage(), rng=random.Random(0))
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    r = await sel.select(["e"])
    assert r.entity_id == "e"


@pytest.mark.asyncio
async def test_explain_with_circuit_breaker_open():
    """When the entity is broken at the CB, explain() reports its state."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cfg = _cfg(
        circuit_breaker=CircuitBreakerConfig(enabled=True, failure_threshold=2)
    )
    sel = build_selector(cfg, redis_client=fake)
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    # Trip the CB.
    await sel._cb.record("e", success=False)
    await sel._cb.record("e", success=False)

    report = await sel.explain("e")
    assert report.circuit_breaker is not None
    assert report.circuit_breaker["open"] is True


@pytest.mark.asyncio
async def test_select_excludes_cb_open_entities():
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cfg = _cfg(
        circuit_breaker=CircuitBreakerConfig(enabled=True, failure_threshold=2)
    )
    sel = build_selector(cfg, redis_client=fake, rng=random.Random(0))
    await sel.upsert_metrics("good", {"conversion": MetricValue(value=0.5)})
    await sel.upsert_metrics("broken", {"conversion": MetricValue(value=0.5)})
    await sel._cb.record("broken", success=False)
    await sel._cb.record("broken", success=False)

    for _ in range(15):
        r = await sel.select(["good", "broken"], SelectionContext(order_id="o"))
        assert r.entity_id == "good"


@pytest.mark.asyncio
async def test_feedback_updates_cb_with_low_reward():
    """A reward < 0.5 records a failure on the breaker."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cfg = _cfg(
        circuit_breaker=CircuitBreakerConfig(enabled=True, failure_threshold=3)
    )
    sel = build_selector(cfg, redis_client=fake)
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    for i in range(3):
        await sel.feedback(f"o-{i}", "e", reward=0.0)
    assert await sel._cb.is_open("e") is True


@pytest.mark.asyncio
async def test_feedback_updates_cb_with_high_reward():
    """A reward >= 0.5 records a success and does not trip."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cfg = _cfg(
        circuit_breaker=CircuitBreakerConfig(enabled=True, failure_threshold=3)
    )
    sel = build_selector(cfg, redis_client=fake)
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    for i in range(10):
        await sel.feedback(f"o-{i}", "e", reward=1.0)
    assert await sel._cb.is_open("e") is False


@pytest.mark.asyncio
async def test_feedback_without_order_id_skips_idempotency():
    """No order_id → still applies the update (every signal counts)."""
    sel = build_selector(_cfg(), storage=MemoryStorage())
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    ok1 = await sel.feedback("", "e", reward=1.0)
    ok2 = await sel.feedback("", "e", reward=1.0)
    assert ok1 is True
    assert ok2 is True


@pytest.mark.asyncio
async def test_public_score_via_stats_argument():
    """get_public_score takes EntityStats directly — handy for callers
    that already loaded the stats and want to avoid an extra round-trip."""
    sel = build_selector(_cfg(), storage=MemoryStorage())
    s = new_stats("e")
    s.metrics["conversion"] = MetricValue(value=0.42)
    score = sel.get_public_score(s)
    assert score == 42


@pytest.mark.asyncio
async def test_select_with_fairness_caps_winner():
    """With a tight max_share, the bandit can't funnel all traffic to one."""
    sel = build_selector(
        _cfg(
            fairness=FairnessConfig(max_share=0.6),
            softmax_temperature=0.05,
            quality_weight=1.0,
            bandit_weight=0.0,
        ),
        storage=MemoryStorage(),
        rng=random.Random(7),
    )
    await sel.upsert_metrics("strong", {"conversion": MetricValue(value=0.99)})
    await sel.upsert_metrics("mid", {"conversion": MetricValue(value=0.5)})
    await sel.upsert_metrics("weak", {"conversion": MetricValue(value=0.01)})
    counts = {"strong": 0, "mid": 0, "weak": 0}
    n = 2000
    for i in range(n):
        r = await sel.select(
            ["strong", "mid", "weak"], SelectionContext(order_id=f"o{i}")
        )
        counts[r.entity_id] += 1
    # Strong should win the most, but bounded by max_share + epsilon.
    assert counts["strong"] / n < 0.7  # capped, not 100%


@pytest.mark.asyncio
async def test_select_records_candidate_breakdown():
    sel = build_selector(_cfg(), storage=MemoryStorage(), rng=random.Random(0))
    await sel.upsert_metrics("a", {"conversion": MetricValue(value=0.5)})
    await sel.upsert_metrics("b", {"conversion": MetricValue(value=0.8)})
    r = await sel.select(["a", "b"], SelectionContext(order_id="o"))
    assert len(r.candidates) == 2
    by_id = {c.entity_id: c for c in r.candidates}
    assert by_id["a"].quality == pytest.approx(0.5)
    assert by_id["b"].quality == pytest.approx(0.8)
    assert sum(c.probability for c in r.candidates) == pytest.approx(1.0)


@pytest.mark.asyncio
async def test_cold_start_records_uniform_breakdown():
    sel = build_selector(
        _cfg(
            cold_start=ColdStartConfig(forced_exploration_orders=5),
        ),
        storage=MemoryStorage(),
        rng=random.Random(0),
    )
    # No entities saved yet → both are cold.
    r = await sel.select(["a", "b"], SelectionContext(order_id="o"))
    assert r.reason == "cold_start"
    assert len(r.candidates) == 2
    assert r.candidates[0].probability == pytest.approx(0.5)
    assert r.candidates[1].probability == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_select_persists_winner_counter():
    sel = build_selector(_cfg(), storage=MemoryStorage())
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    for _ in range(3):
        await sel.select(["e"], SelectionContext(order_id="o"))
    stats = await sel._storage.get("e")
    assert stats.total_selections == 3


@pytest.mark.asyncio
async def test_select_filters_out_disabled_then_returns_remaining():
    sel = build_selector(_cfg(), storage=MemoryStorage(), rng=random.Random(0))
    await sel.upsert_metrics("a", {"conversion": MetricValue(value=0.5)})
    await sel.upsert_metrics("b", {"conversion": MetricValue(value=0.5)})
    await sel.upsert_metrics("c", {"conversion": MetricValue(value=0.5)})
    await sel.disable("a")
    await sel.disable("b")
    for _ in range(10):
        r = await sel.select(["a", "b", "c"], SelectionContext(order_id="o"))
        assert r.entity_id == "c"


@pytest.mark.asyncio
async def test_enable_re_admits_a_previously_disabled_entity():
    sel = build_selector(_cfg(), storage=MemoryStorage(), rng=random.Random(0))
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    await sel.disable("e")
    r = await sel.select(["e"], SelectionContext(order_id="o"))
    assert r.entity_id is None
    await sel.enable("e")
    r = await sel.select(["e"], SelectionContext(order_id="o"))
    assert r.entity_id == "e"


@pytest.mark.asyncio
async def test_enable_disable_on_missing_entity_creates_it():
    """Disable/enable on a never-seen entity creates the stats record."""
    sel = build_selector(_cfg(), storage=MemoryStorage())
    await sel.disable("ghost")
    s = await sel._storage.get("ghost")
    assert s is not None
    assert s.enabled is False
    await sel.enable("ghost")
    s = await sel._storage.get("ghost")
    assert s.enabled is True


@pytest.mark.asyncio
async def test_upsert_metrics_creates_stats_for_unknown_entity():
    sel = build_selector(_cfg(), storage=MemoryStorage())
    await sel.upsert_metrics("new", {"conversion": MetricValue(value=0.3)})
    s = await sel._storage.get("new")
    assert s is not None
    assert s.metrics["conversion"].value == 0.3


@pytest.mark.asyncio
async def test_selector_name_and_config_properties():
    cfg = _cfg(name="custom_name")
    sel = build_selector(cfg, storage=MemoryStorage())
    assert sel.name == "custom_name"
    assert sel.config is cfg
