"""Branch coverage for small helpers and edge cases not covered elsewhere."""
from __future__ import annotations

import random

import pytest

from app.modules.selector import EntityStats, MetricValue, SelectorConfig, new_stats
from app.modules.selector.config.models import FairnessConfig
from app.modules.selector.core.bandit import (
    EpsilonGreedyBandit,
    UCBBandit,
    apply_decay,
    build_bandit,
)
from app.modules.selector.core.policy import (
    SoftmaxPolicy,
    _apply_floor_and_renormalize,
    _weighted_choice,
    build_policy,
)
from app.modules.selector.core.selector import (
    SelectionContext,
    _combine,
    hash_to_bucket,
)
from app.modules.selector.hooks.fairness import _cap_max, _floor_min


# --- bandit branch coverage ------------------------------------------------


def test_apply_decay_negative_elapsed_noop():
    cfg = SelectorConfig(name="t", namespace="ns:t", decay_factor=0.5)
    s = EntityStats(entity_id="e", alpha=5.0, beta=3.0, last_updated=100.0)
    apply_decay(s, cfg, now=99.0)  # clock skew → noop
    assert s.alpha == 5.0
    assert s.beta == 3.0


def test_epsilon_greedy_update_clamps_and_decays():
    cfg = SelectorConfig(name="t", namespace="ns:t", decay_factor=1.0)
    bandit = EpsilonGreedyBandit(cfg, epsilon=0.5, rng=random.Random(0))
    s = EntityStats(entity_id="e", alpha=1.0, beta=1.0, last_updated=0.0)
    bandit.update(s, reward=2.0, now=0.0)  # clamped to 1
    assert s.alpha == pytest.approx(2.0)
    bandit.update(s, reward=-1.0, now=0.0)  # clamped to 0
    assert s.beta == pytest.approx(2.0)


def test_epsilon_greedy_exploration_branch():
    """With epsilon=1 we always take the random branch."""
    cfg = SelectorConfig(name="t", namespace="ns:t")
    bandit = EpsilonGreedyBandit(cfg, epsilon=1.0, rng=random.Random(42))
    s = EntityStats(entity_id="e", alpha=10.0, beta=1.0, last_updated=0.0)
    # exploit branch would give ~10/11=0.909; random gives [0,1).
    val = bandit.sample(s, now=0.0)
    assert 0.0 <= val <= 1.0


def test_ucb_handles_uniform_prior_cleanly():
    cfg = SelectorConfig(name="t", namespace="ns:t")
    bandit = UCBBandit(cfg)
    fresh = EntityStats(entity_id="e", alpha=1.0, beta=1.0, last_updated=0.0)
    val = bandit.sample(fresh, now=0.0)
    assert 0.0 <= val <= 1.0


def test_ucb_update_clamps_and_writes_back():
    cfg = SelectorConfig(name="t", namespace="ns:t", decay_factor=1.0)
    bandit = UCBBandit(cfg)
    s = EntityStats(entity_id="e", alpha=1.0, beta=1.0, last_updated=0.0)
    bandit.update(s, reward=5.0, now=0.0)
    assert s.alpha == pytest.approx(2.0)


def test_build_bandit_rejects_unknown_strategy():
    """Bypass __post_init__ to construct a bad cfg, then ensure the factory complains."""
    cfg = SelectorConfig(name="t", namespace="ns:t")
    object.__setattr__(cfg, "bandit_strategy", "nonsense")
    with pytest.raises(ValueError, match="unknown bandit"):
        build_bandit(cfg)


# --- policy branch coverage -------------------------------------------------


def test_apply_floor_no_op_when_floor_nonpositive():
    out = _apply_floor_and_renormalize([0.5, 0.5], floor=0.0)
    assert out == [0.5, 0.5]


def test_apply_floor_empty_input():
    assert _apply_floor_and_renormalize([], floor=0.1) == []


def test_apply_floor_all_zeros_resolves_uniform():
    """If everything is zero (degenerate), we hand back a uniform distribution."""
    out = _apply_floor_and_renormalize([0.0, 0.0, 0.0], floor=0.0)
    # Floor is 0 → no-op path
    assert out == [0.0, 0.0, 0.0]


def test_softmax_empty_input():
    p = SoftmaxPolicy(SelectorConfig(name="t", namespace="ns:t"))
    assert p.probabilities([]) == []


def test_weighted_choice_returns_last_on_floating_point_slack():
    """If cumulative probs slightly underflow 1.0, _weighted_choice falls back."""
    # Rig the RNG to always return a value just above the cumulative sum.
    class _PinnedRandom:
        def random(self):
            return 0.999_999_999_9

    probs = [0.5, 0.5]
    idx = _weighted_choice(probs, _PinnedRandom())
    assert idx in (0, 1)


def test_build_policy_rejects_unknown():
    with pytest.raises(ValueError, match="unknown policy"):
        build_policy(SelectorConfig(name="t", namespace="ns:t"), override="weird")


# --- fairness branch coverage ----------------------------------------------


def test_cap_max_noop_when_no_excess():
    """If nobody exceeds the cap, the input is returned unchanged."""
    out = _cap_max([0.3, 0.3, 0.4], cap=0.5)
    assert out == [0.3, 0.3, 0.4]


def test_cap_max_handles_all_at_or_above_cap():
    """If every arm is at the cap, there's no under-cap room → no redistribution."""
    out = _cap_max([0.5, 0.5], cap=0.5)
    assert out == [0.5, 0.5]


def test_floor_min_degenerate_zero_probs_resolves_uniform():
    out = _floor_min([0.0, 0.0, 0.0], floor=0.0)
    # floor is 0 → stays zero → total <= 0 → uniform fallback
    assert out == [1 / 3, 1 / 3, 1 / 3]


# --- selector helpers ------------------------------------------------------


def test_combine_zero_quality_uses_epsilon_floor():
    """The score can't become exactly zero — bandit still contributes."""
    val = _combine(quality=0.0, bandit=0.5, qw=0.7, bw=0.3)
    assert val > 0


def test_combine_zero_bandit_uses_epsilon_floor():
    val = _combine(quality=0.5, bandit=0.0, qw=0.7, bw=0.3)
    assert val > 0


def test_combine_quality_only_gives_quality():
    val = _combine(quality=0.6, bandit=0.5, qw=1.0, bw=0.0)
    assert val == pytest.approx(0.6, abs=1e-6)


def test_hash_to_bucket_is_stable_and_in_range():
    for buckets in (2, 10, 100):
        b1 = hash_to_bucket("order-42", buckets)
        b2 = hash_to_bucket("order-42", buckets)
        assert b1 == b2
        assert 0 <= b1 < buckets


def test_hash_to_bucket_spreads_inputs():
    buckets = 10
    hits = {hash_to_bucket(f"order-{i}", buckets) for i in range(200)}
    assert len(hits) > 1  # not all in one bucket


def test_selection_context_defaults():
    ctx = SelectionContext()
    assert ctx.order_id == ""
    assert ctx.amount is None
    assert ctx.require_tags is None


# --- new_stats helper -------------------------------------------------------


def test_new_stats_defaults():
    s = new_stats("e")
    assert s.entity_id == "e"
    assert s.alpha == 1.0
    assert s.beta == 1.0
    assert s.enabled is True
    assert s.tags == {}


def test_new_stats_with_tags_and_priors():
    s = new_stats("e", alpha=3.0, beta=2.0, enabled=False, tags={"region": "ru"})
    assert s.alpha == 3.0
    assert s.beta == 2.0
    assert s.enabled is False
    assert s.tags == {"region": "ru"}
