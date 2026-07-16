"""Offline simulator: replay historical events through a fresh selector."""
from __future__ import annotations

import pytest

from app.modules.selector import MetricValue, SelectorConfig
from app.modules.selector.config.models import MetricSpec
from app.modules.selector.experiments.simulator import (
    HistoricalEvent,
    OfflineSimulator,
)


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


def _event(order_id, chosen, reward, candidates=("a", "b", "c"), metrics=None):
    return HistoricalEvent(
        order_id=order_id,
        candidates=list(candidates),
        chosen_entity_id=chosen,
        reward=reward,
        metrics=metrics or {},
    )


@pytest.mark.asyncio
async def test_empty_trace_returns_zero_report():
    sim = OfflineSimulator(_cfg())
    report = await sim.replay([])
    assert report.total_events == 0
    assert report.coverage == 0.0
    assert report.realised_avg_reward == 0.0


@pytest.mark.asyncio
async def test_replay_seeds_metrics_then_selects():
    cfg = _cfg(quality_weight=1.0, bandit_weight=0.0, softmax_temperature=0.05)
    sim = OfflineSimulator(cfg, rng_seed=0)
    events = [
        _event(
            f"o{i}",
            "high",
            reward=1.0,
            candidates=("high", "low"),
            metrics={
                "high": {"conversion": MetricValue(value=0.95)},
                "low": {"conversion": MetricValue(value=0.05)},
            },
        )
        for i in range(50)
    ]
    report = await sim.replay(events)
    # The new policy is quality-only with cold temp → should pick 'high' most of the time
    # which matches production's choice → high realised coverage.
    assert report.total_events == 50
    assert report.realised_count >= 30
    assert "high" in report.selection_counts
    assert report.coverage > 0.6


@pytest.mark.asyncio
async def test_simulator_records_alpha_beta_per_entity():
    sim = OfflineSimulator(_cfg(), rng_seed=0)
    events = [_event(f"o{i}", "a", 1.0, candidates=("a",)) for i in range(5)]
    report = await sim.replay(events)
    assert "a" in report.alpha_beta
    alpha, beta = report.alpha_beta["a"]
    assert alpha > 1.0  # rewarded


@pytest.mark.asyncio
async def test_counterfactual_count_increments_when_diverges():
    """Force divergence: prod always picked 'a', new policy will spread around."""
    sim = OfflineSimulator(
        _cfg(quality_weight=0.0, bandit_weight=1.0, softmax_temperature=2.0),
        rng_seed=2,
    )
    events = [_event(f"o{i}", "a", 1.0, candidates=("a", "b", "c")) for i in range(60)]
    report = await sim.replay(events)
    # Most events should be counterfactual since the new policy won't always pick 'a'.
    assert report.counterfactual_count + report.realised_count == 60
    assert report.counterfactual_count > 0


@pytest.mark.asyncio
async def test_simulator_skips_events_with_no_candidates_chosen():
    """If the new policy returns no entity (e.g. all filtered), the event
    is counted as total but not affects counterfactual/realised counts."""
    cfg = _cfg()
    sim = OfflineSimulator(cfg, rng_seed=0)
    # Empty candidate pool → engine returns no_candidates.
    report = await sim.replay([_event("o1", "a", 1.0, candidates=())])
    assert report.total_events == 1
    assert report.realised_count == 0
    assert report.counterfactual_count == 0
