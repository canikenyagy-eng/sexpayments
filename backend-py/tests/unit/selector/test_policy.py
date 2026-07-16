import random

import pytest

from app.modules.selector import SelectorConfig
from app.modules.selector.core.policy import (
    ArgmaxPolicy,
    RandomPolicy,
    SoftmaxPolicy,
    build_policy,
)


def _cfg(**kw):
    return SelectorConfig(name="t", namespace="ns:t", **kw)


def test_softmax_probabilities_sum_to_one():
    p = SoftmaxPolicy(_cfg(softmax_temperature=0.5))
    probs = p.probabilities([0.1, 0.5, 0.9])
    assert sum(probs) == pytest.approx(1.0)
    assert all(0 <= x <= 1 for x in probs)


def test_softmax_favors_higher_score():
    p = SoftmaxPolicy(_cfg(softmax_temperature=0.3))
    probs = p.probabilities([0.1, 0.5, 0.9])
    assert probs[2] > probs[1] > probs[0]


def test_softmax_uniform_when_scores_equal():
    p = SoftmaxPolicy(_cfg(softmax_temperature=0.5))
    probs = p.probabilities([0.5, 0.5, 0.5])
    assert all(x == pytest.approx(1 / 3) for x in probs)


def test_softmax_high_temperature_is_more_uniform():
    cold = SoftmaxPolicy(_cfg(softmax_temperature=0.05)).probabilities([0.1, 0.9])
    hot = SoftmaxPolicy(_cfg(softmax_temperature=5.0)).probabilities([0.1, 0.9])
    assert cold[1] > hot[1]  # cold concentrates on the winner
    assert hot[1] - hot[0] < cold[1] - cold[0]


def test_softmax_min_exploration_floor():
    """Floor + renormalize lifts the starved arms close to (slightly below) the floor."""
    no_floor = SoftmaxPolicy(_cfg(softmax_temperature=0.05)).probabilities(
        [0.0, 0.0, 1.0]
    )
    with_floor = SoftmaxPolicy(
        _cfg(softmax_temperature=0.05, min_exploration_prob=0.1)
    ).probabilities([0.0, 0.0, 1.0])
    assert min(with_floor) > min(no_floor)  # lift applied
    assert sum(with_floor) == pytest.approx(1.0)


def test_softmax_choose_picks_within_distribution():
    p = SoftmaxPolicy(
        _cfg(softmax_temperature=0.5), rng=random.Random(123)
    )
    counts = [0, 0, 0]
    for _ in range(2000):
        idx = p.choose([0.1, 0.5, 0.9])
        counts[idx] += 1
    assert counts[2] > counts[1] > counts[0]


def test_argmax_deterministic():
    p = ArgmaxPolicy()
    assert p.choose([0.1, 0.9, 0.5]) == 1
    probs = p.probabilities([0.1, 0.9, 0.5])
    assert probs == [0.0, 1.0, 0.0]


def test_random_policy_uniform():
    p = RandomPolicy(rng=random.Random(0))
    probs = p.probabilities([0.1, 0.5, 0.9])
    assert all(x == pytest.approx(1 / 3) for x in probs)


def test_random_policy_chooses_within_range():
    p = RandomPolicy(rng=random.Random(0))
    for _ in range(20):
        idx = p.choose([0.1, 0.5, 0.9])
        assert 0 <= idx <= 2


def test_empty_scores():
    assert SoftmaxPolicy(_cfg()).probabilities([]) == []
    assert ArgmaxPolicy().probabilities([]) == []
    assert RandomPolicy().probabilities([]) == []


def test_build_policy_dispatch():
    assert isinstance(build_policy(_cfg(policy="softmax")), SoftmaxPolicy)
    assert isinstance(build_policy(_cfg(policy="argmax")), ArgmaxPolicy)
    assert isinstance(build_policy(_cfg(policy="random")), RandomPolicy)


def test_build_policy_override_wins():
    p = build_policy(_cfg(policy="softmax"), override="random")
    assert isinstance(p, RandomPolicy)
