"""IPS / SNIPS / DM / DR estimators."""
from __future__ import annotations

import pytest

from app.modules.selector.experiments.counterfactual import (
    LoggedEvent,
    clip_weights,
    direct_method,
    doubly_robust,
    ips,
    snips,
)


def _ev(*, reward, prop, new_prob, r_old=None, r_new=None):
    return LoggedEvent(
        order_id="o",
        chosen_entity_id="a",
        candidates=["a", "b"],
        reward=reward,
        propensity=prop,
        new_action_prob=new_prob,
        reward_estimate_old=r_old,
        reward_estimate_new=r_new,
    )


def test_ips_empty_input_zero():
    assert ips([]) == 0.0


def test_ips_no_action_in_new_policy():
    """If new policy never chooses what was logged (new_prob=0), value is 0."""
    events = [_ev(reward=1.0, prop=0.5, new_prob=0.0)] * 5
    assert ips(events) == 0.0


def test_ips_matches_logging_policy_recovers_average_reward():
    events = [_ev(reward=1.0, prop=0.5, new_prob=0.5)] * 10
    # weight=1, average reward 1.0
    assert ips(events) == pytest.approx(1.0)


def test_ips_skips_zero_propensity():
    events = [
        _ev(reward=1.0, prop=0.0, new_prob=0.5),
        _ev(reward=1.0, prop=0.5, new_prob=0.5),
    ]
    # First is dropped, second has weight=1.0 reward=1.0 → 1.0
    assert ips(events) == pytest.approx(1.0)


def test_snips_self_normalises():
    events = [_ev(reward=1.0, prop=0.5, new_prob=1.0)] * 5
    # weight = 2 each, weighted_reward = 10, weight_sum = 10 → 1.0
    assert snips(events) == pytest.approx(1.0)


def test_snips_handles_no_weight():
    """When all propensities are 0, SNIPS returns 0 (not NaN)."""
    events = [_ev(reward=1.0, prop=0.0, new_prob=1.0)] * 3
    assert snips(events) == 0.0


def test_direct_method_averages_estimate():
    events = [
        _ev(reward=1.0, prop=0.5, new_prob=0.5, r_new=0.8),
        _ev(reward=0.0, prop=0.5, new_prob=0.5, r_new=0.2),
    ]
    assert direct_method(events) == pytest.approx(0.5)


def test_direct_method_skips_missing_estimates():
    events = [
        _ev(reward=1.0, prop=0.5, new_prob=0.5, r_new=0.6),
        _ev(reward=0.0, prop=0.5, new_prob=0.5, r_new=None),
    ]
    assert direct_method(events) == pytest.approx(0.6)


def test_direct_method_no_events():
    assert direct_method([]) == 0.0


def test_doubly_robust_combines_dm_and_ips():
    """If r̂ is perfect (matches observed reward), DR == DM."""
    events = [
        _ev(reward=1.0, prop=0.5, new_prob=0.5, r_old=1.0, r_new=1.0),
        _ev(reward=0.0, prop=0.5, new_prob=0.5, r_old=0.0, r_new=0.0),
    ]
    # residual = w * (reward - r_old) = w * 0 = 0 → DR == DM
    assert doubly_robust(events) == pytest.approx(direct_method(events))


def test_doubly_robust_skips_incomplete_events():
    events = [
        _ev(reward=1.0, prop=0.5, new_prob=0.5, r_old=None, r_new=0.5),
        _ev(reward=1.0, prop=0.5, new_prob=0.5, r_old=0.5, r_new=0.7),
    ]
    assert doubly_robust(events) == pytest.approx(0.7 + (1.0 - 0.5))


def test_doubly_robust_no_valid_events_zero():
    assert doubly_robust([]) == 0.0


def test_clip_weights_caps_extreme_ratios():
    events = [
        _ev(reward=1.0, prop=0.01, new_prob=1.0),  # weight 100
        _ev(reward=1.0, prop=0.5, new_prob=0.5),   # weight 1
    ]
    clipped = clip_weights(events, max_weight=5.0)
    # First event's new_prob scaled down: target weight = 5 → scale = 5/100 = 0.05
    assert clipped[0].new_action_prob == pytest.approx(0.05)
    assert clipped[1].new_action_prob == pytest.approx(0.5)


def test_clip_weights_passes_zero_propensity_through():
    events = [_ev(reward=1.0, prop=0.0, new_prob=0.5)]
    clipped = clip_weights(events, max_weight=10.0)
    assert clipped[0].propensity == 0.0
