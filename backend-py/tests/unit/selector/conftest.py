"""Selector-test fixtures.

Selector tests don't need the DB session or app.main, just a few small
config builders. We keep them local to this subtree.
"""
from __future__ import annotations

import random
from typing import Any

import pytest

from app.modules.selector import (
    EntitySelector,
    MemoryStorage,
    SelectorConfig,
    build_memory_selector,
    load_from_dict,
)


def _base_config(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "namespace": "sel:test",
        "enabled": True,
        "quality_weight": 0.7,
        "bandit_weight": 0.3,
        "bandit_strategy": "thompson",
        "policy": "softmax",
        "softmax_temperature": 0.5,
        "min_exploration_prob": 0.0,
        "decay_factor": 0.995,
        "decay_interval_sec": 60,
        "metrics": [
            {
                "name": "conversion",
                "weight": 1.0,
                "min_value": 0.0,
                "max_value": 1.0,
                "higher_is_better": True,
            }
        ],
    }
    base.update(overrides)
    return base


@pytest.fixture
def make_config():
    def _make(**overrides: Any) -> SelectorConfig:
        root = load_from_dict({"selectors": {"test": _base_config(**overrides)}})
        return root.get("test")

    return _make


@pytest.fixture
def make_selector(make_config):
    def _make(*, seed: int = 42, **overrides: Any) -> EntitySelector:
        cfg = make_config(**overrides)
        return build_memory_selector(cfg, rng=random.Random(seed))

    return _make


@pytest.fixture
def memory_storage():
    return MemoryStorage()
