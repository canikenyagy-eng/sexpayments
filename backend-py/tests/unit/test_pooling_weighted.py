import random
from app.common.enums.pooling import PoolingStrategy


def _weighted_pick(candidates):
    weights = [c["priority_score"] for c in candidates]
    return (random.choices(candidates, weights=weights, k=1)[0]
            if sum(weights) > 0 else random.choice(candidates))


def test_weighted_distribution_is_proportional():
    random.seed(42)
    cands = [{"id": 1, "priority_score": 200.0}, {"id": 2, "priority_score": 100.0}]
    counts = {1: 0, 2: 0}
    for _ in range(6000):
        counts[_weighted_pick(cands)["id"]] += 1
    ratio = counts[1] / counts[2]
    assert 1.7 < ratio < 2.3   # ~2:1


def test_weighted_all_zero_falls_back_uniform():
    random.seed(1)
    cands = [{"id": 1, "priority_score": 0.0}, {"id": 2, "priority_score": 0.0}]
    picks = {_weighted_pick(cands)["id"] for _ in range(50)}
    assert picks == {1, 2}


def test_weighted_is_a_strategy_member():
    assert PoolingStrategy("weighted") is PoolingStrategy.WEIGHTED
