"""Smoke tests for the Celery task wrapper.

We exercise the async helpers directly rather than going through Celery —
no point spinning up a broker in unit tests. The wrappers' job is to call
the right coroutines under the registered selectors, which is what we test.
"""
from __future__ import annotations

import pytest

from app.modules.selector import bootstrap, load_from_dict, MemoryStorage, SelectorRegistry
from app.modules.selector.recompute.aggregator import CallableAggregator
from app.modules.selector.recompute.feedback_timeout import FeedbackTimeoutJob
from app.modules.selector.recompute.metrics_job import MetricsRecomputer


@pytest.fixture(autouse=True)
def _bootstrap_clean():
    bootstrap.reset_for_tests()
    yield
    bootstrap.reset_for_tests()


@pytest.mark.asyncio
async def test_recompute_all_skips_when_no_jobs_wired():
    from app.workers.tasks.selector import _recompute_all

    # Nothing registered → no-op, no exception.
    await _recompute_all()


@pytest.mark.asyncio
async def test_recompute_all_runs_each_registered_recomputer():
    cfg = load_from_dict({"selectors": {"t": {}}})
    registry = SelectorRegistry(cfg, storage_overrides={"t": MemoryStorage()})
    bootstrap.set_registry(registry)
    sel = registry.get("t")
    # Seed an entity so list_entity_ids() returns something.
    from app.modules.selector import MetricValue

    await sel.upsert_metrics("x", {})

    seen = {"calls": 0}

    async def fetch(ids, window):
        seen["calls"] += 1
        return {}

    bootstrap.register_recomputer(
        MetricsRecomputer(sel, CallableAggregator(fetch))
    )

    from app.workers.tasks.selector import _recompute_all

    await _recompute_all()
    assert seen["calls"] == 1


@pytest.mark.asyncio
async def test_recompute_all_continues_when_one_recomputer_fails():
    cfg = load_from_dict({"selectors": {"t": {}}})
    registry = SelectorRegistry(cfg, storage_overrides={"t": MemoryStorage()})
    bootstrap.set_registry(registry)
    sel = registry.get("t")
    await sel.upsert_metrics("x", {})  # seed for list_entity_ids()

    class _Broken:
        async def run(self):
            raise RuntimeError("boom")

    seen = {"calls": 0}

    async def fetch(ids, window):
        seen["calls"] += 1
        return {}

    bootstrap.register_recomputer(_Broken())  # type: ignore[arg-type]
    bootstrap.register_recomputer(
        MetricsRecomputer(sel, CallableAggregator(fetch))
    )

    from app.workers.tasks.selector import _recompute_all

    await _recompute_all()  # must not propagate
    assert seen["calls"] == 1


@pytest.mark.asyncio
async def test_sweep_timeouts_skips_when_no_jobs_wired():
    from app.workers.tasks.selector import _sweep_timeouts

    await _sweep_timeouts()


@pytest.mark.asyncio
async def test_sweep_timeouts_swallows_failures():
    class _Broken:
        async def sweep(self):
            raise RuntimeError("redis down")

    bootstrap.register_timeout_job(_Broken())  # type: ignore[arg-type]

    from app.workers.tasks.selector import _sweep_timeouts

    await _sweep_timeouts()  # must not raise
