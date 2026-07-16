"""Prometheus instrumentation for the selector subsystem.

Naming follows §9.1 of the TZ. Labels are kept low-cardinality:
  - ``name`` is the selector identifier (``traders``, ``providers``, …)
  - per-entity gauges are intentionally limited via ``set_entity_gauges``
    so we don't blow up cardinality when there are thousands of entities

Histograms use buckets sized for our SLO (p99 < 50ms select, < 30ms feedback).
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram


_LATENCY_BUCKETS = (
    0.001, 0.002, 0.005, 0.01, 0.02, 0.03, 0.05, 0.1, 0.25, 0.5, 1.0,
)


select_total = Counter(
    "selector_select_total",
    "Number of select() calls, labelled by selector and outcome.",
    labelnames=("name", "result"),
)

select_latency_seconds = Histogram(
    "selector_select_latency_seconds",
    "select() wall-clock latency in seconds.",
    labelnames=("name",),
    buckets=_LATENCY_BUCKETS,
)

feedback_total = Counter(
    "selector_feedback_total",
    "Number of feedback() calls, labelled by selector / signal / reward bucket.",
    labelnames=("name", "signal", "reward_bucket"),
)

feedback_latency_seconds = Histogram(
    "selector_feedback_latency_seconds",
    "feedback() wall-clock latency in seconds.",
    labelnames=("name",),
    buckets=_LATENCY_BUCKETS,
)

fallback_total = Counter(
    "selector_fallback_total",
    "How often we fell back to random/uniform due to a backend failure.",
    labelnames=("name", "reason"),
)

active_entities = Gauge(
    "selector_active_entities",
    "Count of entities that are currently enabled per selector.",
    labelnames=("name",),
)

circuit_breaker_open_total = Counter(
    "selector_circuit_breaker_open_total",
    "Number of circuit-breaker trips, per selector.",
    labelnames=("name",),
)

selection_entropy = Gauge(
    "selector_selection_entropy",
    "Shannon entropy of the most recent selection probability distribution.",
    labelnames=("name",),
)

config_version = Gauge(
    "selector_config_version",
    "Monotonic counter incremented on each config reload.",
    labelnames=("name",),
)

entity_alpha = Gauge(
    "selector_entity_alpha",
    "Per-entity Beta-distribution α (success pseudo-count). High-cardinality "
    "label — set only via set_entity_gauges() for a curated subset.",
    labelnames=("name", "entity_id"),
)

entity_beta = Gauge(
    "selector_entity_beta",
    "Per-entity Beta-distribution β (failure pseudo-count).",
    labelnames=("name", "entity_id"),
)

entity_quality_score = Gauge(
    "selector_entity_quality_score",
    "Per-entity quality score [0, 1].",
    labelnames=("name", "entity_id"),
)


def reward_bucket(reward: float) -> str:
    """Reduce a continuous reward to a small label set."""
    if reward <= 0.0:
        return "0"
    if reward <= 0.25:
        return "low"
    if reward <= 0.5:
        return "mid"
    if reward <= 0.75:
        return "high"
    return "1"
