"""Configuration dataclasses for the selector subsystem.

A SelectorConfig fully describes one selector instance (e.g. "traders",
"providers"). Configs are loaded from YAML at startup and may be hot-reloaded
without redeploying the service.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class MetricSpec:
    name: str
    weight: float
    min_value: float
    max_value: float
    higher_is_better: bool = True
    min_sample_size: int = 0
    default_value: Optional[float] = None
    max_age_sec: Optional[int] = None

    def __post_init__(self) -> None:
        if self.weight < 0:
            raise ValueError(f"metric {self.name!r}: weight must be >= 0")
        if self.max_value <= self.min_value:
            raise ValueError(
                f"metric {self.name!r}: max_value must be > min_value"
            )


@dataclass(frozen=True)
class CircuitBreakerConfig:
    enabled: bool = False
    failure_threshold: int = 5
    window_sec: int = 60
    cooldown_sec: int = 300


@dataclass(frozen=True)
class FairnessConfig:
    min_share: float = 0.0
    max_share: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_share <= 1.0:
            raise ValueError("min_share must be in [0, 1]")
        if not 0.0 <= self.max_share <= 1.0:
            raise ValueError("max_share must be in [0, 1]")
        if self.min_share > self.max_share:
            raise ValueError("min_share must be <= max_share")


@dataclass(frozen=True)
class ColdStartConfig:
    forced_exploration_orders: int = 0
    bootstrap_alpha: float = 1.0
    bootstrap_beta: float = 1.0


@dataclass(frozen=True)
class RewardSignals:
    accepted: float = 0.3
    completed: float = 0.7
    timeout_sec: int = 3600


@dataclass(frozen=True)
class ExperimentVariant:
    weight: int
    policy: Optional[str] = None


@dataclass(frozen=True)
class ExperimentConfig:
    name: str
    enabled: bool
    variants: dict[str, ExperimentVariant]
    primary_metric: Optional[str] = None


@dataclass(frozen=True)
class SelectorConfig:
    name: str
    namespace: str
    enabled: bool = True

    quality_weight: float = 0.7
    bandit_weight: float = 0.3

    bandit_strategy: str = "thompson"
    policy: str = "softmax"

    decay_factor: float = 0.995
    decay_interval_sec: int = 60
    initial_alpha: float = 1.0
    initial_beta: float = 1.0

    softmax_temperature: float = 0.5
    min_exploration_prob: float = 0.0

    metrics: tuple[MetricSpec, ...] = ()
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    fairness: FairnessConfig = field(default_factory=FairnessConfig)
    cold_start: ColdStartConfig = field(default_factory=ColdStartConfig)
    reward: RewardSignals = field(default_factory=RewardSignals)
    experiments: tuple[ExperimentConfig, ...] = ()

    def __post_init__(self) -> None:
        if self.quality_weight < 0 or self.bandit_weight < 0:
            raise ValueError("quality_weight and bandit_weight must be >= 0")
        if self.quality_weight + self.bandit_weight == 0:
            raise ValueError("quality_weight + bandit_weight must be > 0")
        if self.bandit_strategy not in {"thompson", "epsilon_greedy", "ucb"}:
            raise ValueError(f"unknown bandit_strategy {self.bandit_strategy!r}")
        if self.policy not in {"softmax", "argmax", "random"}:
            raise ValueError(f"unknown policy {self.policy!r}")
        if not 0.0 < self.decay_factor <= 1.0:
            raise ValueError("decay_factor must be in (0, 1]")
        if self.decay_interval_sec <= 0:
            raise ValueError("decay_interval_sec must be > 0")
        if self.softmax_temperature <= 0:
            raise ValueError("softmax_temperature must be > 0")
        if not 0.0 <= self.min_exploration_prob <= 1.0:
            raise ValueError("min_exploration_prob must be in [0, 1]")

    def metric_by_name(self, name: str) -> Optional[MetricSpec]:
        for m in self.metrics:
            if m.name == name:
                return m
        return None


@dataclass(frozen=True)
class RootConfig:
    """Top-level config holding all selectors keyed by name."""
    selectors: dict[str, SelectorConfig]

    def get(self, name: str) -> SelectorConfig:
        try:
            return self.selectors[name]
        except KeyError as exc:
            raise KeyError(f"selector {name!r} is not configured") from exc
