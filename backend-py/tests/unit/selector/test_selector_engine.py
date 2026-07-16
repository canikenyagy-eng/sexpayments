import asyncio
import random

import pytest

from app.modules.selector import MetricValue, SelectionContext


@pytest.mark.asyncio
async def test_empty_pool_returns_no_candidates(make_selector):
    sel = make_selector()
    result = await sel.select([], SelectionContext(order_id="o"))
    assert result.entity_id is None
    assert result.reason == "no_candidates"


@pytest.mark.asyncio
async def test_disabled_selector_returns_disabled(make_selector):
    sel = make_selector(enabled=False)
    result = await sel.select(["a", "b"], SelectionContext(order_id="o"))
    assert result.entity_id is None
    assert result.reason == "selector_disabled"


@pytest.mark.asyncio
async def test_single_candidate_always_chosen(make_selector):
    sel = make_selector()
    await sel.upsert_metrics("only", {"conversion": MetricValue(value=0.5)})
    result = await sel.select(["only"], SelectionContext(order_id="o"))
    assert result.entity_id == "only"
    assert result.reason == "selected"


@pytest.mark.asyncio
async def test_disabled_entity_excluded(make_selector):
    sel = make_selector()
    await sel.upsert_metrics("a", {"conversion": MetricValue(value=0.5)})
    await sel.upsert_metrics("b", {"conversion": MetricValue(value=0.5)})
    await sel.disable("a")
    for _ in range(20):
        result = await sel.select(["a", "b"], SelectionContext(order_id="o"))
        assert result.entity_id == "b"


@pytest.mark.asyncio
async def test_all_disabled_returns_all_filtered(make_selector):
    sel = make_selector()
    await sel.upsert_metrics("a", {"conversion": MetricValue(value=0.5)})
    await sel.disable("a")
    result = await sel.select(["a"], SelectionContext(order_id="o"))
    assert result.entity_id is None
    assert result.reason == "all_filtered"


@pytest.mark.asyncio
async def test_higher_quality_wins_more_often(make_selector):
    sel = make_selector(seed=42, softmax_temperature=0.3, bandit_weight=0.0, quality_weight=1.0)
    await sel.upsert_metrics("good", {"conversion": MetricValue(value=0.95, sample_size=100)})
    await sel.upsert_metrics("bad", {"conversion": MetricValue(value=0.05, sample_size=100)})

    counts = {"good": 0, "bad": 0}
    for i in range(500):
        result = await sel.select(["good", "bad"], SelectionContext(order_id=f"o{i}"))
        counts[result.entity_id] += 1
    assert counts["good"] > counts["bad"] * 4


@pytest.mark.asyncio
async def test_feedback_idempotent_by_order_id(make_selector):
    sel = make_selector()
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    ok1 = await sel.feedback("o1", "e", reward=1.0)
    ok2 = await sel.feedback("o1", "e", reward=1.0)
    assert ok1 is True
    assert ok2 is False  # duplicate suppressed


@pytest.mark.asyncio
async def test_feedback_creates_stats_if_missing(make_selector):
    sel = make_selector()
    ok = await sel.feedback("o1", "fresh-entity", reward=1.0)
    assert ok is True
    explain = await sel.explain("fresh-entity")
    assert explain.stats is not None
    assert explain.stats.alpha > 1.0


@pytest.mark.asyncio
async def test_feedback_shifts_bandit_distribution(make_selector):
    sel = make_selector(seed=1, quality_weight=0.0, bandit_weight=1.0)
    await sel.upsert_metrics("e1", {"conversion": MetricValue(value=0.5)})
    await sel.upsert_metrics("e2", {"conversion": MetricValue(value=0.5)})
    # Reward e1 heavily so its alpha grows.
    for i in range(50):
        await sel.feedback(f"win-{i}", "e1", reward=1.0)
    # And punish e2.
    for i in range(50):
        await sel.feedback(f"lose-{i}", "e2", reward=0.0)
    counts = {"e1": 0, "e2": 0}
    for i in range(500):
        r = await sel.select(["e1", "e2"], SelectionContext(order_id=f"sel-{i}"))
        counts[r.entity_id] += 1
    assert counts["e1"] > counts["e2"] * 3


@pytest.mark.asyncio
async def test_explain_returns_full_breakdown(make_selector):
    sel = make_selector()
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.8, sample_size=200)})
    report = await sel.explain("e")
    assert report.stats is not None
    assert report.quality_score == pytest.approx(0.8)
    assert report.bandit_mean == pytest.approx(0.5)
    assert len(report.metric_breakdown) == 1
    assert report.metric_breakdown[0].name == "conversion"


@pytest.mark.asyncio
async def test_explain_missing_entity(make_selector):
    sel = make_selector()
    report = await sel.explain("nope")
    assert report.stats is None
    assert report.quality_score is None
    assert report.metric_breakdown == []


@pytest.mark.asyncio
async def test_cold_start_forced_exploration(make_selector):
    """Cold-start gates: while any candidate has < forced_exploration_orders
    selections, the warm one is excluded entirely from the pick.
    """
    sel = make_selector(
        seed=0,
        cold_start={"forced_exploration_orders": 5, "bootstrap_alpha": 1.0, "bootstrap_beta": 1.0},
    )
    # Pre-populate one entity that's already past cold start.
    await sel.upsert_metrics("warm", {"conversion": MetricValue(value=0.9, sample_size=100)})
    warm_stats = await sel._storage.get("warm")
    warm_stats.total_selections = 100
    await sel._storage.save(warm_stats)

    # First 10 selections: colds should always win because they're still
    # under the forced_exploration threshold.
    chosen_during_cold = []
    for i in range(10):
        r = await sel.select(["warm", "cold1", "cold2"], SelectionContext(order_id=f"o{i}"))
        chosen_during_cold.append(r.entity_id)
    assert "warm" not in chosen_during_cold


@pytest.mark.asyncio
async def test_concurrent_selects_safe(make_selector):
    """Concurrent selects on the in-memory storage shouldn't crash or corrupt state."""
    sel = make_selector(seed=7)
    await sel.upsert_metrics("a", {"conversion": MetricValue(value=0.5)})
    await sel.upsert_metrics("b", {"conversion": MetricValue(value=0.5)})

    async def one(i):
        return await sel.select(["a", "b"], SelectionContext(order_id=f"c-{i}"))

    results = await asyncio.gather(*(one(i) for i in range(100)))
    chosen = [r.entity_id for r in results]
    assert all(c in ("a", "b") for c in chosen)


@pytest.mark.asyncio
async def test_segment_filter_excludes_mismatched_tags(make_selector):
    sel = make_selector()
    await sel.upsert_metrics("ru", {"conversion": MetricValue(value=0.5)})
    await sel.upsert_metrics("us", {"conversion": MetricValue(value=0.5)})
    # Manually set tags
    ru = await sel._storage.get("ru")
    ru.tags["region"] = "ru"
    await sel._storage.save(ru)
    us = await sel._storage.get("us")
    us.tags["region"] = "us"
    await sel._storage.save(us)

    for _ in range(10):
        r = await sel.select(
            ["ru", "us"],
            SelectionContext(order_id="o", require_tags={"region": "ru"}),
        )
        assert r.entity_id == "ru"


@pytest.mark.asyncio
async def test_get_public_score_is_int_0_100(make_selector):
    sel = make_selector()
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.73)})
    stats = await sel._storage.get("e")
    score = sel.get_public_score(stats)
    assert isinstance(score, int)
    assert 0 <= score <= 100
    assert score == 73


@pytest.mark.asyncio
async def test_selection_records_latency(make_selector):
    sel = make_selector()
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    r = await sel.select(["e"], SelectionContext(order_id="o"))
    assert r.latency_ms >= 0
    assert r.latency_ms < 100  # should be near-instant for memory storage
