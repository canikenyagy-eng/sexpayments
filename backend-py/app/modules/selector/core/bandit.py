"""Multi-armed bandit strategies.

Thompson sampling is the default. We model each entity as a Beta(α, β) arm
whose mean represents "probability of success on this entity". On feedback,
α += reward and β += (1 - reward), so a reward of 1 reinforces the success
count and a reward of 0 reinforces the failure count.

α/β decay exponentially toward the (1, 1) uniform prior between updates so
old performance doesn't dominate forever — older data weighs less, recent
data more.
"""
from __future__ import annotations

import math
import random
from typing import Protocol

from app.modules.selector.config.models import SelectorConfig
from app.modules.selector.core.stats import EntityStats


class BanditStrategy(Protocol):
    """Anything that can score an arm and absorb a reward signal."""

    def sample(self, stats: EntityStats, *, now: float) -> float: ...

    def update(self, stats: EntityStats, reward: float, *, now: float) -> None: ...


def _decay_factor(config: SelectorConfig, elapsed: float) -> float:
    if elapsed <= 0:
        return 1.0
    steps = elapsed / config.decay_interval_sec
    return config.decay_factor ** steps


def apply_decay(stats: EntityStats, config: SelectorConfig, *, now: float) -> None:
    """Pull α and β toward the uniform (1, 1) prior by the elapsed-time factor.

    The math: (alpha - 1) is the "accumulated success count" above the prior;
    we multiply it by γ^(elapsed / interval) so it half-lives back to the
    prior over time. Same for (beta - 1).
    """
    elapsed = now - stats.last_updated
    if elapsed <= 0:
        return
    factor = _decay_factor(config, elapsed)
    stats.alpha = 1.0 + (stats.alpha - 1.0) * factor
    stats.beta = 1.0 + (stats.beta - 1.0) * factor
    stats.last_updated = now


class ThompsonBandit(BanditStrategy):
    def __init__(self, config: SelectorConfig, rng: random.Random | None = None):
        self._config = config
        self._rng = rng or random.Random()

    def sample(self, stats: EntityStats, *, now: float) -> float:
        apply_decay(stats, self._config, now=now)
        return self._rng.betavariate(stats.alpha, stats.beta)

    def update(self, stats: EntityStats, reward: float, *, now: float) -> None:
        reward = max(0.0, min(1.0, reward))
        apply_decay(stats, self._config, now=now)
        stats.alpha += reward
        stats.beta += 1.0 - reward
        stats.last_updated = now


class EpsilonGreedyBandit(BanditStrategy):
    """ε-greedy fallback: exploit the posterior mean, explore at random with prob ε."""

    def __init__(
        self,
        config: SelectorConfig,
        *,
        epsilon: float = 0.1,
        rng: random.Random | None = None,
    ):
        self._config = config
        self._epsilon = epsilon
        self._rng = rng or random.Random()

    def sample(self, stats: EntityStats, *, now: float) -> float:
        apply_decay(stats, self._config, now=now)
        if self._rng.random() < self._epsilon:
            return self._rng.random()
        return stats.alpha / (stats.alpha + stats.beta)

    def update(self, stats: EntityStats, reward: float, *, now: float) -> None:
        reward = max(0.0, min(1.0, reward))
        apply_decay(stats, self._config, now=now)
        stats.alpha += reward
        stats.beta += 1.0 - reward
        stats.last_updated = now


class UCBBandit(BanditStrategy):
    """UCB1-style: posterior mean + an exploration bonus that shrinks with selections.

    Bonus uses total_selections across the pool so under-explored arms get a
    boost relative to well-trodden ones.
    """

    def __init__(self, config: SelectorConfig, *, c: float = 1.4):
        self._config = config
        self._c = c

    def sample(self, stats: EntityStats, *, now: float) -> float:
        apply_decay(stats, self._config, now=now)
        n = stats.alpha + stats.beta - 1.0
        if n <= 0:
            return 1.0
        mean = stats.alpha / (stats.alpha + stats.beta)
        bonus = self._c * math.sqrt(math.log(max(n, 2.0)) / max(n, 1.0))
        return min(1.0, mean + bonus)

    def update(self, stats: EntityStats, reward: float, *, now: float) -> None:
        reward = max(0.0, min(1.0, reward))
        apply_decay(stats, self._config, now=now)
        stats.alpha += reward
        stats.beta += 1.0 - reward
        stats.last_updated = now


def build_bandit(
    config: SelectorConfig, *, rng: random.Random | None = None
) -> BanditStrategy:
    if config.bandit_strategy == "thompson":
        return ThompsonBandit(config, rng=rng)
    if config.bandit_strategy == "epsilon_greedy":
        return EpsilonGreedyBandit(config, rng=rng)
    if config.bandit_strategy == "ucb":
        return UCBBandit(config)
    raise ValueError(f"unknown bandit strategy: {config.bandit_strategy}")
