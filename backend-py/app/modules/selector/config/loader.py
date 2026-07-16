"""YAML / dict-based loader for selector configuration.

PyYAML is an optional dep: when it's missing we still accept a Python ``dict``
(useful for tests). The dict shape mirrors the YAML schema documented in the
selector module README.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Mapping

from app.modules.selector.config.models import (
    CircuitBreakerConfig,
    ColdStartConfig,
    ExperimentConfig,
    ExperimentVariant,
    FairnessConfig,
    MetricSpec,
    RewardSignals,
    RootConfig,
    SelectorConfig,
)


def _coerce_metric(raw: Mapping[str, Any]) -> MetricSpec:
    return MetricSpec(
        name=str(raw["name"]),
        weight=float(raw["weight"]),
        min_value=float(raw["min_value"]),
        max_value=float(raw["max_value"]),
        higher_is_better=bool(raw.get("higher_is_better", True)),
        min_sample_size=int(raw.get("min_sample_size", 0)),
        default_value=(
            float(raw["default_value"]) if raw.get("default_value") is not None else None
        ),
        max_age_sec=(
            int(raw["max_age_sec"]) if raw.get("max_age_sec") is not None else None
        ),
    )


def _coerce_circuit_breaker(raw: Mapping[str, Any] | None) -> CircuitBreakerConfig:
    if not raw:
        return CircuitBreakerConfig()
    return CircuitBreakerConfig(
        enabled=bool(raw.get("enabled", False)),
        failure_threshold=int(raw.get("failure_threshold", 5)),
        window_sec=int(raw.get("window_sec", 60)),
        cooldown_sec=int(raw.get("cooldown_sec", 300)),
    )


def _coerce_fairness(raw: Mapping[str, Any] | None) -> FairnessConfig:
    if not raw:
        return FairnessConfig()
    return FairnessConfig(
        min_share=float(raw.get("min_share", 0.0)),
        max_share=float(raw.get("max_share", 1.0)),
    )


def _coerce_cold_start(raw: Mapping[str, Any] | None) -> ColdStartConfig:
    if not raw:
        return ColdStartConfig()
    return ColdStartConfig(
        forced_exploration_orders=int(raw.get("forced_exploration_orders", 0)),
        bootstrap_alpha=float(raw.get("bootstrap_alpha", 1.0)),
        bootstrap_beta=float(raw.get("bootstrap_beta", 1.0)),
    )


def _coerce_reward(raw: Mapping[str, Any] | None) -> RewardSignals:
    if not raw:
        return RewardSignals()
    signals = raw.get("partial_signals", {}) or {}
    return RewardSignals(
        accepted=float(signals.get("accepted", 0.3)),
        completed=float(signals.get("completed", 0.7)),
        timeout_sec=int(raw.get("timeout_sec", 3600)),
    )


def _coerce_experiment(raw: Mapping[str, Any]) -> ExperimentConfig:
    variants = {
        vname: ExperimentVariant(
            weight=int(vraw["weight"]),
            policy=(str(vraw["policy"]) if "policy" in vraw else None),
        )
        for vname, vraw in (raw.get("variants") or {}).items()
    }
    return ExperimentConfig(
        name=str(raw["name"]),
        enabled=bool(raw.get("enabled", False)),
        variants=variants,
        primary_metric=raw.get("primary_metric"),
    )


def _coerce_selector(name: str, raw: Mapping[str, Any]) -> SelectorConfig:
    return SelectorConfig(
        name=name,
        namespace=str(raw.get("namespace", f"sel:{name}")),
        enabled=bool(raw.get("enabled", True)),
        quality_weight=float(raw.get("quality_weight", 0.7)),
        bandit_weight=float(raw.get("bandit_weight", 0.3)),
        bandit_strategy=str(raw.get("bandit_strategy", "thompson")),
        policy=str(raw.get("policy", "softmax")),
        decay_factor=float(raw.get("decay_factor", 0.995)),
        decay_interval_sec=int(raw.get("decay_interval_sec", 60)),
        initial_alpha=float(raw.get("initial_alpha", 1.0)),
        initial_beta=float(raw.get("initial_beta", 1.0)),
        softmax_temperature=float(raw.get("softmax_temperature", 0.5)),
        min_exploration_prob=float(raw.get("min_exploration_prob", 0.0)),
        metrics=tuple(_coerce_metric(m) for m in raw.get("metrics", []) or []),
        circuit_breaker=_coerce_circuit_breaker(raw.get("circuit_breaker")),
        fairness=_coerce_fairness(raw.get("fairness")),
        cold_start=_coerce_cold_start(raw.get("cold_start")),
        reward=_coerce_reward(raw.get("reward")),
        experiments=tuple(
            _coerce_experiment(e) for e in raw.get("experiments", []) or []
        ),
    )


def load_from_dict(data: Mapping[str, Any]) -> RootConfig:
    """Build a RootConfig from a plain Python mapping (test-friendly)."""
    selectors_raw = data.get("selectors") or {}
    selectors = {
        name: _coerce_selector(name, raw) for name, raw in selectors_raw.items()
    }
    return RootConfig(selectors=selectors)


def load_from_yaml(path: str | os.PathLike[str]) -> RootConfig:
    """Read a YAML file and produce a RootConfig.

    Requires PyYAML — install it with ``pip install pyyaml``.
    """
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        raise RuntimeError(
            "load_from_yaml requires PyYAML; install with 'pip install pyyaml'"
        ) from exc

    text = Path(path).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, Mapping):
        raise ValueError(f"YAML root must be a mapping, got {type(data).__name__}")
    return load_from_dict(data)
