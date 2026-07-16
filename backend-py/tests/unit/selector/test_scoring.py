import time

import pytest

from app.modules.selector import EntityStats, MetricSpec, MetricValue, SelectorConfig
from app.modules.selector.core.scoring import QualityScorer


def _cfg(metrics: list[MetricSpec]) -> SelectorConfig:
    return SelectorConfig(name="t", namespace="ns:t", metrics=tuple(metrics))


def test_score_uniform_when_no_metrics():
    cfg = SelectorConfig(name="t", namespace="ns:t", metrics=())
    scorer = QualityScorer(cfg)
    stats = EntityStats(entity_id="e1")
    assert scorer.score(stats) == 1.0


def test_score_higher_is_better():
    cfg = _cfg([MetricSpec(name="m", weight=1.0, min_value=0.0, max_value=10.0)])
    scorer = QualityScorer(cfg)
    s = EntityStats(entity_id="e1", metrics={"m": MetricValue(value=7.5, sample_size=1)})
    assert scorer.score(s) == pytest.approx(0.75)


def test_score_higher_is_worse_inverts():
    cfg = _cfg([
        MetricSpec(
            name="dispute",
            weight=1.0,
            min_value=0.0,
            max_value=1.0,
            higher_is_better=False,
        )
    ])
    scorer = QualityScorer(cfg)
    s = EntityStats(entity_id="e", metrics={"dispute": MetricValue(value=0.2)})
    assert scorer.score(s) == pytest.approx(0.8)


def test_score_clamps_out_of_range():
    cfg = _cfg([MetricSpec(name="m", weight=1.0, min_value=0.0, max_value=10.0)])
    scorer = QualityScorer(cfg)
    above = EntityStats(entity_id="e", metrics={"m": MetricValue(value=20.0)})
    below = EntityStats(entity_id="e", metrics={"m": MetricValue(value=-5.0)})
    assert scorer.score(above) == 1.0
    assert scorer.score(below) == 0.0


def test_undersampled_metric_uses_default():
    cfg = _cfg([
        MetricSpec(
            name="m",
            weight=1.0,
            min_value=0.0,
            max_value=1.0,
            min_sample_size=100,
            default_value=0.5,
        )
    ])
    scorer = QualityScorer(cfg)
    s = EntityStats(
        entity_id="e", metrics={"m": MetricValue(value=0.99, sample_size=3)}
    )
    # 0.99 ignored, default 0.5 used instead
    assert scorer.score(s) == pytest.approx(0.5)


def test_undersampled_no_default_is_skipped():
    cfg = _cfg([
        MetricSpec(
            name="primary", weight=0.7, min_value=0.0, max_value=1.0
        ),
        MetricSpec(
            name="rare",
            weight=0.3,
            min_value=0.0,
            max_value=1.0,
            min_sample_size=1000,
        ),
    ])
    scorer = QualityScorer(cfg)
    s = EntityStats(
        entity_id="e",
        metrics={
            "primary": MetricValue(value=1.0, sample_size=100),
            "rare": MetricValue(value=1.0, sample_size=10),
        },
    )
    # rare metric skipped (no default), so only primary contributes:
    # 0.7 * 1.0 / total_weight(1.0) = 0.7
    assert scorer.score(s) == pytest.approx(0.7)


def test_stale_metric_falls_back_to_default():
    cfg = _cfg([
        MetricSpec(
            name="m",
            weight=1.0,
            min_value=0.0,
            max_value=1.0,
            max_age_sec=10,
            default_value=0.2,
        )
    ])
    scorer = QualityScorer(cfg)
    s = EntityStats(
        entity_id="e",
        metrics={"m": MetricValue(value=0.9, sample_size=100, updated_at=0.0)},
    )
    assert scorer.score(s, now=time.time()) == pytest.approx(0.2)


def test_explain_breakdown_includes_skipped():
    cfg = _cfg([
        MetricSpec(name="ok", weight=0.5, min_value=0.0, max_value=1.0),
        MetricSpec(
            name="missing", weight=0.5, min_value=0.0, max_value=1.0
        ),
    ])
    scorer = QualityScorer(cfg)
    s = EntityStats(entity_id="e", metrics={"ok": MetricValue(value=0.6)})
    score, breakdown = scorer.explain(s)
    assert score == pytest.approx(0.6 * 0.5 / 1.0)
    names = {b.name: b for b in breakdown}
    assert names["missing"].skipped_reason == "missing"
    assert names["missing"].contribution == 0.0
    assert names["ok"].contribution > 0
