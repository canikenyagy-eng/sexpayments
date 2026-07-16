import random

import pytest

from app.modules.selector import EntityStats, SelectorConfig
from app.modules.selector.core.bandit import (
    EpsilonGreedyBandit,
    ThompsonBandit,
    UCBBandit,
    apply_decay,
    build_bandit,
)


def _cfg(**kw):
    return SelectorConfig(name="t", namespace="ns:t", **kw)


def test_thompson_sample_in_range():
    bandit = ThompsonBandit(_cfg(), rng=random.Random(0))
    s = EntityStats(entity_id="e", alpha=2.0, beta=3.0, last_updated=100.0)
    for _ in range(50):
        v = bandit.sample(s, now=100.0)
        assert 0.0 <= v <= 1.0


def test_thompson_update_reinforces_success():
    bandit = ThompsonBandit(_cfg(), rng=random.Random(0))
    s = EntityStats(entity_id="e", alpha=1.0, beta=1.0, last_updated=100.0)
    bandit.update(s, reward=1.0, now=100.0)
    assert s.alpha == pytest.approx(2.0)
    assert s.beta == pytest.approx(1.0)


def test_thompson_update_reinforces_failure():
    bandit = ThompsonBandit(_cfg(), rng=random.Random(0))
    s = EntityStats(entity_id="e", alpha=1.0, beta=1.0, last_updated=100.0)
    bandit.update(s, reward=0.0, now=100.0)
    assert s.alpha == pytest.approx(1.0)
    assert s.beta == pytest.approx(2.0)


def test_thompson_partial_reward():
    bandit = ThompsonBandit(_cfg(), rng=random.Random(0))
    s = EntityStats(entity_id="e", alpha=1.0, beta=1.0, last_updated=100.0)
    bandit.update(s, reward=0.3, now=100.0)
    assert s.alpha == pytest.approx(1.3)
    assert s.beta == pytest.approx(1.7)


def test_thompson_clamps_reward():
    bandit = ThompsonBandit(_cfg(), rng=random.Random(0))
    s = EntityStats(entity_id="e", alpha=1.0, beta=1.0, last_updated=100.0)
    bandit.update(s, reward=5.0, now=100.0)
    assert s.alpha == pytest.approx(2.0)
    s2 = EntityStats(entity_id="e", alpha=1.0, beta=1.0, last_updated=100.0)
    bandit.update(s2, reward=-3.0, now=100.0)
    assert s2.beta == pytest.approx(2.0)


def test_decay_pulls_toward_prior():
    cfg = _cfg(decay_factor=0.5, decay_interval_sec=60)
    # alpha=5 (means +4 above prior), beta=3 (means +2 above prior)
    # one decay interval: factor=0.5 -> alpha=1 + 4*0.5=3, beta=1+2*0.5=2
    s = EntityStats(entity_id="e", alpha=5.0, beta=3.0, last_updated=0.0)
    apply_decay(s, cfg, now=60.0)
    assert s.alpha == pytest.approx(3.0)
    assert s.beta == pytest.approx(2.0)
    assert s.last_updated == pytest.approx(60.0)


def test_decay_noop_when_no_elapsed_time():
    cfg = _cfg(decay_factor=0.5)
    s = EntityStats(entity_id="e", alpha=5.0, beta=3.0, last_updated=100.0)
    apply_decay(s, cfg, now=100.0)
    assert s.alpha == 5.0
    assert s.beta == 3.0


def test_thompson_distribution_favors_winner_in_long_run():
    """With ~1000 samples, a Beta(50, 5) arm beats Beta(5, 50) the vast majority of the time."""
    bandit = ThompsonBandit(_cfg(decay_factor=1.0), rng=random.Random(0))
    winner = EntityStats(entity_id="w", alpha=50.0, beta=5.0, last_updated=0.0)
    loser = EntityStats(entity_id="l", alpha=5.0, beta=50.0, last_updated=0.0)
    wins = 0
    for _ in range(1000):
        # Re-create stats per iteration so decay doesn't move them.
        w = EntityStats(entity_id="w", alpha=50.0, beta=5.0, last_updated=0.0)
        l = EntityStats(entity_id="l", alpha=5.0, beta=50.0, last_updated=0.0)
        if bandit.sample(w, now=0.0) > bandit.sample(l, now=0.0):
            wins += 1
    assert wins > 950  # winner should clearly dominate


def test_epsilon_greedy_exploits_then_explores():
    bandit = EpsilonGreedyBandit(_cfg(), epsilon=0.0, rng=random.Random(0))
    s = EntityStats(entity_id="e", alpha=3.0, beta=1.0, last_updated=0.0)
    # epsilon=0 -> always exploit -> always return posterior mean
    val = bandit.sample(s, now=0.0)
    assert val == pytest.approx(0.75, abs=1e-9)


def test_ucb_bonus_shrinks_with_data():
    bandit = UCBBandit(_cfg(), c=1.4)
    new = EntityStats(entity_id="new", alpha=1.0, beta=1.0, last_updated=0.0)
    seasoned = EntityStats(entity_id="old", alpha=50.0, beta=50.0, last_updated=0.0)
    # both have mean=0.5, but the new one should have a larger UCB score.
    assert bandit.sample(new, now=0.0) >= bandit.sample(seasoned, now=0.0)


def test_build_bandit_known_strategies():
    for strat in ("thompson", "epsilon_greedy", "ucb"):
        bandit = build_bandit(_cfg(bandit_strategy=strat))
        assert bandit is not None
