"""MetricsRecomputer + FeedbackTimeoutJob behavior."""
from __future__ import annotations

import asyncio
import time

import fakeredis.aioredis
import pytest

from app.modules.selector import (
    MetricValue,
    SelectorConfig,
    build_memory_selector,
    build_selector,
    new_stats,
)
from app.modules.selector.config.models import MetricSpec, RewardSignals
from app.modules.selector.recompute.aggregator import CallableAggregator
from app.modules.selector.recompute.feedback_timeout import FeedbackTimeoutJob
from app.modules.selector.recompute.metrics_job import MetricsRecomputer


def _cfg(**kw):
    base = dict(
        name="t",
        namespace="ns:t",
        metrics=(
            MetricSpec(name="conversion", weight=1.0, min_value=0.0, max_value=1.0),
        ),
    )
    base.update(kw)
    return SelectorConfig(**base)


# --- MetricsRecomputer -----------------------------------------------------


@pytest.mark.asyncio
async def test_recompute_updates_metrics_from_aggregator():
    sel = build_memory_selector(_cfg())
    # Seed entities so list_entity_ids() returns them.
    await sel.upsert_metrics("a", {"conversion": MetricValue(value=0.1)})
    await sel.upsert_metrics("b", {"conversion": MetricValue(value=0.1)})

    async def fetch(ids, window):
        return {
            "a": {"conversion": MetricValue(value=0.8, sample_size=42)},
            "b": {"conversion": MetricValue(value=0.5, sample_size=42)},
        }

    recomputer = MetricsRecomputer(sel, CallableAggregator(fetch))
    report = await recomputer.run()
    assert report.entities_updated == 2
    assert report.metrics_written == 2
    sa = await sel._storage.get("a")
    assert sa.metrics["conversion"].value == pytest.approx(0.8)


@pytest.mark.asyncio
async def test_recompute_skips_empty_metric_sets():
    sel = build_memory_selector(_cfg())
    await sel.upsert_metrics("a", {"conversion": MetricValue(value=0.1)})

    async def fetch(ids, window):
        return {"a": {}}

    recomputer = MetricsRecomputer(sel, CallableAggregator(fetch))
    report = await recomputer.run()
    assert report.entities_updated == 1
    assert report.metrics_written == 0  # nothing actually written


@pytest.mark.asyncio
async def test_recompute_handles_aggregator_failure():
    sel = build_memory_selector(_cfg())
    await sel.upsert_metrics("a", {"conversion": MetricValue(value=0.1)})

    async def broken(ids, window):
        raise RuntimeError("ch unreachable")

    recomputer = MetricsRecomputer(sel, CallableAggregator(broken))
    report = await recomputer.run()
    assert report.entities_updated == 0
    assert report.metrics_written == 0


@pytest.mark.asyncio
async def test_recompute_with_explicit_resolver():
    sel = build_memory_selector(_cfg())

    async def resolver():
        return ["new_entity"]

    async def fetch(ids, window):
        assert list(ids) == ["new_entity"]
        return {"new_entity": {"conversion": MetricValue(value=0.42)}}

    recomputer = MetricsRecomputer(
        sel, CallableAggregator(fetch), entity_resolver=resolver
    )
    report = await recomputer.run()
    assert report.entities_updated == 1
    stats = await sel._storage.get("new_entity")
    assert stats.metrics["conversion"].value == pytest.approx(0.42)


@pytest.mark.asyncio
async def test_recompute_sync_resolver_also_works():
    """Resolver can be a regular function returning an iterable."""
    sel = build_memory_selector(_cfg())

    def resolver():
        return iter(["x"])

    async def fetch(ids, window):
        return {"x": {"conversion": MetricValue(value=0.3)}}

    recomputer = MetricsRecomputer(
        sel, CallableAggregator(fetch), entity_resolver=resolver
    )
    report = await recomputer.run()
    assert report.entities_updated == 1


@pytest.mark.asyncio
async def test_recompute_no_entities_returns_zero_report():
    sel = build_memory_selector(_cfg())

    async def fetch(ids, window):
        return {}

    recomputer = MetricsRecomputer(sel, CallableAggregator(fetch))
    report = await recomputer.run()
    assert report.entities_updated == 0
    assert report.metrics_written == 0


@pytest.mark.asyncio
async def test_recompute_continues_when_one_upsert_fails():
    sel = build_memory_selector(_cfg())
    await sel.upsert_metrics("a", {"conversion": MetricValue(value=0.1)})
    await sel.upsert_metrics("b", {"conversion": MetricValue(value=0.1)})

    original = sel._storage.save
    calls = {"n": 0}

    async def flaky_save(s):
        calls["n"] += 1
        if calls["n"] == 1:
            # Fail first save during upsert_metrics for 'a'.
            raise RuntimeError("redis hiccup")
        await original(s)

    sel._storage.save = flaky_save  # type: ignore[assignment]

    async def fetch(ids, window):
        return {
            "a": {"conversion": MetricValue(value=0.5)},
            "b": {"conversion": MetricValue(value=0.6)},
        }

    recomputer = MetricsRecomputer(sel, CallableAggregator(fetch))
    report = await recomputer.run()
    assert report.metrics_written >= 1  # at least 'b' got through


# --- FeedbackTimeoutJob ----------------------------------------------------


@pytest.fixture
def fake_redis():
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


@pytest.mark.asyncio
async def test_timeout_sweep_no_entries_returns_zero(fake_redis):
    cfg = _cfg(reward=RewardSignals(timeout_sec=10))
    sel = build_selector(cfg, redis_client=fake_redis)
    job = FeedbackTimeoutJob(sel, fake_redis)
    report = await job.sweep()
    assert report.swept == 0


@pytest.mark.asyncio
async def test_timeout_sweep_picks_up_old_entries(fake_redis):
    cfg = _cfg(reward=RewardSignals(timeout_sec=1))
    sel = build_selector(cfg, redis_client=fake_redis)
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    job = FeedbackTimeoutJob(sel, fake_redis)
    await job.mark_pending("o-old", "e")

    # Sweep "in the future" — pretend it's well past the timeout.
    report = await job.sweep(now=time.time() + 100)
    assert report.swept == 1
    assert report.fed_back == 1


@pytest.mark.asyncio
async def test_timeout_sweep_skips_recent_entries(fake_redis):
    cfg = _cfg(reward=RewardSignals(timeout_sec=3600))
    sel = build_selector(cfg, redis_client=fake_redis)
    job = FeedbackTimeoutJob(sel, fake_redis)
    await job.mark_pending("o-fresh", "e")
    report = await job.sweep()
    assert report.swept == 0


@pytest.mark.asyncio
async def test_mark_pending_ignores_empty_order(fake_redis):
    cfg = _cfg()
    sel = build_selector(cfg, redis_client=fake_redis)
    job = FeedbackTimeoutJob(sel, fake_redis)
    await job.mark_pending("", "e")  # must not raise
    report = await job.sweep(now=time.time() + 10_000)
    assert report.swept == 0


@pytest.mark.asyncio
async def test_acknowledge_removes_pending(fake_redis):
    cfg = _cfg()
    sel = build_selector(cfg, redis_client=fake_redis)
    job = FeedbackTimeoutJob(sel, fake_redis)
    await job.mark_pending("o1", "e")
    await job.acknowledge("o1")
    report = await job.sweep(now=time.time() + 10_000)
    assert report.swept == 0


@pytest.mark.asyncio
async def test_redis_errors_swallowed_in_mark_and_sweep():
    """Both mark_pending and sweep must never propagate Redis errors."""

    class _BoomRedis:
        async def zadd(self, *a, **kw):
            raise RuntimeError("boom")

        async def zrangebyscore(self, *a, **kw):
            raise RuntimeError("boom")

        async def zremrangebyscore(self, *a, **kw):
            raise RuntimeError("boom")

        async def zrange(self, *a, **kw):
            raise RuntimeError("boom")

        async def zrem(self, *a, **kw):
            raise RuntimeError("boom")

    cfg = _cfg()
    sel = build_memory_selector(cfg)
    job = FeedbackTimeoutJob(sel, _BoomRedis())
    await job.mark_pending("o", "e")
    report = await job.sweep()
    assert report.swept == 0
    await job.acknowledge("o")
