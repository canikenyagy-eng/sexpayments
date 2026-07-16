"""Quality score: a static [0, 1] aggregation of business metrics.

The bandit handles the dynamic exploration/exploitation tradeoff. Quality
captures everything we already "know" about an entity from its observed
business KPIs (conversion, dispute rate, ...).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from app.modules.selector.config.models import MetricSpec, SelectorConfig
from app.modules.selector.core.stats import EntityStats, MetricValue


@dataclass
class ScoreBreakdown:
    """Per-metric contribution to the final quality score (for explain())."""
    name: str
    raw_value: Optional[float]
    normalized: float
    weight: float
    contribution: float
    skipped_reason: Optional[str] = None


def _normalize(spec: MetricSpec, raw: float) -> float:
    span = spec.max_value - spec.min_value
    x = (raw - spec.min_value) / span
    if x < 0.0:
        x = 0.0
    elif x > 1.0:
        x = 1.0
    if not spec.higher_is_better:
        x = 1.0 - x
    return x


def _evaluate_metric(
    spec: MetricSpec,
    mv: Optional[MetricValue],
    now: float,
) -> tuple[float, Optional[float], Optional[str]]:
    """Return (normalized_value, raw_value_or_None, skip_reason_or_None).

    skip_reason != None means the metric was missing/stale/undersampled AND no
    default was provided. The metric simply doesn't contribute in that case.
    """
    raw: Optional[float] = None if mv is None else mv.value
    reason: Optional[str] = None

    if mv is None:
        reason = "missing"
    elif spec.min_sample_size and mv.sample_size < spec.min_sample_size:
        reason = "undersampled"
    elif spec.max_age_sec is not None and (now - mv.updated_at) > spec.max_age_sec:
        reason = "stale"

    if reason is not None:
        if spec.default_value is None:
            return 0.0, raw, reason
        raw = spec.default_value
        reason = None

    return _normalize(spec, raw), raw, reason


class QualityScorer:
    def __init__(self, config: SelectorConfig):
        self._config = config
        total_w = sum(m.weight for m in config.metrics)
        self._total_weight = total_w
        self._has_metrics = total_w > 0

    def score(self, stats: EntityStats, *, now: Optional[float] = None) -> float:
        if not self._has_metrics:
            return 1.0
        now_ts = time.time() if now is None else now
        accum = 0.0
        for spec in self._config.metrics:
            mv = stats.metrics.get(spec.name)
            normalized, _, skip = _evaluate_metric(spec, mv, now_ts)
            if skip is not None:
                continue
            accum += spec.weight * normalized
        return accum / self._total_weight

    def explain(
        self, stats: EntityStats, *, now: Optional[float] = None
    ) -> tuple[float, list[ScoreBreakdown]]:
        now_ts = time.time() if now is None else now
        breakdown: list[ScoreBreakdown] = []
        accum = 0.0
        for spec in self._config.metrics:
            mv = stats.metrics.get(spec.name)
            normalized, raw, skip = _evaluate_metric(spec, mv, now_ts)
            contribution = 0.0
            if skip is None:
                contribution = spec.weight * normalized
                accum += contribution
            breakdown.append(
                ScoreBreakdown(
                    name=spec.name,
                    raw_value=raw,
                    normalized=normalized,
                    weight=spec.weight,
                    contribution=contribution,
                    skipped_reason=skip,
                )
            )
        final = accum / self._total_weight if self._has_metrics else 1.0
        return final, breakdown
