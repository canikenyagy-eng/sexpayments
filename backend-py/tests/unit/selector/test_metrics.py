"""Prometheus instrumentation smoke tests.

We can't easily assert on Prometheus internals, but we can confirm:
  - The counter/histogram/gauge objects exist with the expected names and labels.
  - The ``reward_bucket`` helper maps continuous reward into the right buckets.
  - Calling .labels(...).inc() works (i.e. label names are coherent).
"""
from __future__ import annotations

import pytest

from app.modules.selector import metrics as m


def test_reward_bucket_boundaries():
    assert m.reward_bucket(0.0) == "0"
    assert m.reward_bucket(-1.0) == "0"
    assert m.reward_bucket(0.1) == "low"
    assert m.reward_bucket(0.25) == "low"
    assert m.reward_bucket(0.3) == "mid"
    assert m.reward_bucket(0.5) == "mid"
    assert m.reward_bucket(0.6) == "high"
    assert m.reward_bucket(0.75) == "high"
    assert m.reward_bucket(0.9) == "1"
    assert m.reward_bucket(1.0) == "1"


def test_counter_label_signature():
    m.select_total.labels(name="t", result="selected").inc()
    m.feedback_total.labels(name="t", signal="completed", reward_bucket="1").inc()
    m.fallback_total.labels(name="t", reason="redis_down").inc()
    m.circuit_breaker_open_total.labels(name="t").inc()


def test_histogram_observe():
    m.select_latency_seconds.labels(name="t").observe(0.01)
    m.feedback_latency_seconds.labels(name="t").observe(0.005)


def test_gauges_set():
    m.active_entities.labels(name="t").set(42)
    m.selection_entropy.labels(name="t").set(1.23)
    m.config_version.labels(name="t").set(5)
    m.entity_alpha.labels(name="t", entity_id="e").set(2.5)
    m.entity_beta.labels(name="t", entity_id="e").set(1.5)
    m.entity_quality_score.labels(name="t", entity_id="e").set(0.7)


def test_label_signature_mismatch_raises():
    with pytest.raises(ValueError):
        m.select_total.labels(name="t").inc()  # missing 'result'
