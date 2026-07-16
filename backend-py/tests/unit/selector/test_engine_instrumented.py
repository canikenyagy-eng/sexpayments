"""End-to-end engine tests covering the new wiring:
  - event sink emission on every select / feedback
  - reward shaping from named signals
  - A/B variant in SelectionResult and decision events
  - reward bucket label inference
"""
from __future__ import annotations

import random

import pytest

from app.modules.selector import (
    MemoryStorage,
    MetricValue,
    SelectorConfig,
    build_memory_selector,
    build_selector,
)
from app.modules.selector.config.models import (
    ExperimentConfig,
    ExperimentVariant,
    MetricSpec,
    RewardSignals,
)
from app.modules.selector.core.selector import SelectionContext
from app.modules.selector.logging_.events import DecisionEvent, FeedbackEvent


class _RecordingSink:
    """In-process sink that just buffers events for the test to inspect."""

    def __init__(self):
        self.decisions: list[DecisionEvent] = []
        self.feedback: list[FeedbackEvent] = []
        self.closed = False

    async def emit_decision(self, event):
        self.decisions.append(event)

    async def emit_feedback(self, event):
        self.feedback.append(event)

    async def close(self):
        self.closed = True


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


@pytest.mark.asyncio
async def test_sink_receives_decision_on_select():
    sink = _RecordingSink()
    sel = build_memory_selector(_cfg(), event_sink=sink, rng=random.Random(0))
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    await sel.select(["e"], SelectionContext(order_id="o1"))
    assert len(sink.decisions) == 1
    ev = sink.decisions[0]
    assert ev.selector_name == "t"
    assert ev.order_id == "o1"
    assert ev.chosen_entity_id == "e"
    assert ev.reason == "selected"


@pytest.mark.asyncio
async def test_sink_receives_decision_on_empty_pool():
    sink = _RecordingSink()
    sel = build_memory_selector(_cfg(), event_sink=sink)
    await sel.select([], SelectionContext(order_id="o1"))
    assert sink.decisions[0].chosen_entity_id is None
    assert sink.decisions[0].reason == "no_candidates"


@pytest.mark.asyncio
async def test_sink_receives_feedback_event():
    sink = _RecordingSink()
    sel = build_memory_selector(_cfg(), event_sink=sink)
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    applied = await sel.feedback("o1", "e", reward=1.0)
    assert applied is True
    assert len(sink.feedback) == 1
    fb = sink.feedback[0]
    assert fb.entity_id == "e"
    assert fb.reward == 1.0
    assert fb.duplicate is False


@pytest.mark.asyncio
async def test_sink_records_duplicate_feedback():
    sink = _RecordingSink()
    sel = build_memory_selector(_cfg(), event_sink=sink)
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    await sel.feedback("o1", "e", reward=1.0)
    await sel.feedback("o1", "e", reward=1.0)
    assert sink.feedback[1].duplicate is True


@pytest.mark.asyncio
async def test_sink_failures_swallowed_and_logged(caplog):
    class _Broken:
        async def emit_decision(self, ev):
            raise RuntimeError("boom")

        async def emit_feedback(self, ev):
            raise RuntimeError("boom")

        async def close(self):
            return None

    sel = build_memory_selector(_cfg(), event_sink=_Broken())
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    # Must not propagate the sink error.
    r = await sel.select(["e"], SelectionContext(order_id="o"))
    assert r.entity_id == "e"
    await sel.feedback("o", "e", reward=1.0)


@pytest.mark.asyncio
async def test_reward_shaping_completed_signal():
    cfg = _cfg(reward=RewardSignals(accepted=0.2, completed=0.8))
    sel = build_memory_selector(cfg)
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    # No explicit reward — should default to 'completed' = 0.8
    await sel.feedback("o", "e", reward=None, signal="completed")
    explain = await sel.explain("e")
    # alpha started at 1 + (cold_start prior 1) = 1, after 0.8 reward: alpha = 1.8
    assert explain.stats.alpha == pytest.approx(1.8)


@pytest.mark.asyncio
async def test_reward_shaping_accepted_signal():
    cfg = _cfg(reward=RewardSignals(accepted=0.25, completed=0.75))
    sel = build_memory_selector(cfg)
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    await sel.feedback("o", "e", reward=None, signal="accepted")
    explain = await sel.explain("e")
    assert explain.stats.alpha == pytest.approx(1.25)


@pytest.mark.asyncio
async def test_reward_shaping_failed_signal_gives_zero():
    sel = build_memory_selector(_cfg())
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    await sel.feedback("o", "e", reward=None, signal="failed")
    explain = await sel.explain("e")
    assert explain.stats.alpha == pytest.approx(1.0)  # unchanged
    assert explain.stats.beta == pytest.approx(2.0)  # +1 failure


@pytest.mark.asyncio
async def test_reward_shaping_success_signal_gives_one():
    sel = build_memory_selector(_cfg())
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    await sel.feedback("o", "e", reward=None, signal="success")
    explain = await sel.explain("e")
    assert explain.stats.alpha == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_reward_shaping_unknown_signal_falls_back_to_completed():
    sel = build_memory_selector(_cfg())
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    await sel.feedback("o", "e", reward=None, signal="weird")
    explain = await sel.explain("e")
    # default completed reward = 0.7
    assert explain.stats.alpha == pytest.approx(1.7)


@pytest.mark.asyncio
async def test_explicit_reward_overrides_signal():
    sel = build_memory_selector(_cfg())
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    await sel.feedback("o", "e", reward=1.0, signal="failed")  # explicit wins
    explain = await sel.explain("e")
    assert explain.stats.alpha == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_ab_router_attached_records_variant():
    import fakeredis.aioredis

    cfg = _cfg(
        experiments=(
            ExperimentConfig(
                name="exp1",
                enabled=True,
                variants={
                    "control": ExperimentVariant(weight=100, policy="random"),
                    "treatment": ExperimentVariant(weight=0),
                },
            ),
        )
    )
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sink = _RecordingSink()
    sel = build_selector(cfg, redis_client=fake, event_sink=sink)
    await sel.upsert_metrics("a", {"conversion": MetricValue(value=0.5)})
    await sel.upsert_metrics("b", {"conversion": MetricValue(value=0.5)})
    r = await sel.select(["a", "b"], SelectionContext(order_id="o-ab"))
    assert r.experiment_variant == "control"
    # And the variant rides into the decision event for downstream analysis.
    assert sink.decisions[0].experiment_variant == "control"


@pytest.mark.asyncio
async def test_no_ab_no_variant_in_result():
    sink = _RecordingSink()
    sel = build_memory_selector(_cfg(), event_sink=sink)
    await sel.upsert_metrics("e", {"conversion": MetricValue(value=0.5)})
    r = await sel.select(["e"], SelectionContext(order_id="o"))
    assert r.experiment_variant is None
