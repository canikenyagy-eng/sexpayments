"""Selection policies: turn a vector of final scores into one chosen index.

- Softmax: weighted random by exp(score / temperature), so higher-scoring
  entities are picked more often but every active arm keeps non-zero mass.
- Argmax: deterministic; takes the top score (ties broken by entity_id).
- Random: ignores scores; uniform pick. Used as the A/B control or as a
  graceful-degradation fallback.
"""
from __future__ import annotations

import math
import random
from typing import Protocol, Sequence

from app.modules.selector.config.models import SelectorConfig


class SelectionPolicy(Protocol):
    def probabilities(self, scores: Sequence[float]) -> list[float]: ...

    def choose(self, scores: Sequence[float]) -> int: ...


def _apply_floor_and_renormalize(
    probs: list[float], floor: float
) -> list[float]:
    """Lift every probability to at least ``floor``, then renormalize to sum=1."""
    if floor <= 0 or not probs:
        return probs
    probs = [max(p, floor) for p in probs]
    total = sum(probs)
    if total <= 0:
        return [1.0 / len(probs)] * len(probs)
    return [p / total for p in probs]


class SoftmaxPolicy(SelectionPolicy):
    def __init__(
        self,
        config: SelectorConfig,
        rng: random.Random | None = None,
    ):
        self._temperature = config.softmax_temperature
        self._min_explore = config.min_exploration_prob
        self._rng = rng or random.Random()

    def probabilities(self, scores: Sequence[float]) -> list[float]:
        if not scores:
            return []
        # Subtract max for numerical stability before exp.
        scaled = [s / self._temperature for s in scores]
        m = max(scaled)
        exps = [math.exp(x - m) for x in scaled]
        total = sum(exps)
        if total <= 0:
            return [1.0 / len(scores)] * len(scores)
        probs = [e / total for e in exps]
        if self._min_explore > 0:
            floor = self._min_explore / len(scores)
            probs = _apply_floor_and_renormalize(probs, floor)
        return probs

    def choose(self, scores: Sequence[float]) -> int:
        probs = self.probabilities(scores)
        return _weighted_choice(probs, self._rng)


class ArgmaxPolicy(SelectionPolicy):
    def probabilities(self, scores: Sequence[float]) -> list[float]:
        if not scores:
            return []
        # Argmax assigns all probability mass to the winner.
        probs = [0.0] * len(scores)
        best = max(range(len(scores)), key=lambda i: scores[i])
        probs[best] = 1.0
        return probs

    def choose(self, scores: Sequence[float]) -> int:
        return max(range(len(scores)), key=lambda i: scores[i])


class RandomPolicy(SelectionPolicy):
    def __init__(self, rng: random.Random | None = None):
        self._rng = rng or random.Random()

    def probabilities(self, scores: Sequence[float]) -> list[float]:
        n = len(scores)
        if n == 0:
            return []
        return [1.0 / n] * n

    def choose(self, scores: Sequence[float]) -> int:
        return self._rng.randrange(len(scores))


def _weighted_choice(probs: Sequence[float], rng: random.Random) -> int:
    r = rng.random()
    cumulative = 0.0
    last_idx = 0
    for i, p in enumerate(probs):
        cumulative += p
        last_idx = i
        if r < cumulative:
            return i
    # Floating-point slack: return the last index if we somehow didn't land.
    return last_idx


def build_policy(
    config: SelectorConfig,
    *,
    rng: random.Random | None = None,
    override: str | None = None,
) -> SelectionPolicy:
    name = override or config.policy
    if name == "softmax":
        return SoftmaxPolicy(config, rng=rng)
    if name == "argmax":
        return ArgmaxPolicy()
    if name == "random":
        return RandomPolicy(rng=rng)
    raise ValueError(f"unknown policy: {name}")
