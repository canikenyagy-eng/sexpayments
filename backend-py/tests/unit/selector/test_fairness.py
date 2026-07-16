import pytest

from app.modules.selector.config.models import FairnessConfig
from app.modules.selector.hooks.fairness import apply_fairness


def test_noop_when_within_bounds():
    cfg = FairnessConfig(min_share=0.0, max_share=1.0)
    probs = [0.5, 0.3, 0.2]
    assert apply_fairness(probs, cfg) == probs


def test_min_share_lifts_floor():
    cfg = FairnessConfig(min_share=0.3, max_share=1.0)  # floor = 0.1 per arm
    probs = [0.9, 0.05, 0.05]
    out = apply_fairness(probs, cfg)
    assert sum(out) == pytest.approx(1.0)
    # Min share lifts and renormalizes, so each starved arm gets close to
    # the floor (a hair below after renormalization).
    assert min(out) > min(probs)
    assert out[1] == out[2]  # symmetric lift
    assert out[0] < probs[0]  # winner gives up some mass


def test_max_share_caps_winner():
    cfg = FairnessConfig(min_share=0.0, max_share=0.5)
    probs = [0.9, 0.05, 0.05]
    out = apply_fairness(probs, cfg)
    assert sum(out) == pytest.approx(1.0)
    assert out[0] <= 0.5 + 1e-9


def test_combined_min_and_max():
    cfg = FairnessConfig(min_share=0.3, max_share=0.5)
    probs = [0.95, 0.04, 0.01]
    out = apply_fairness(probs, cfg)
    assert sum(out) == pytest.approx(1.0)
    assert out[0] <= 0.5 + 1e-9


def test_empty_input():
    assert apply_fairness([], FairnessConfig()) == []
