"""Loader edge cases for both dict and YAML inputs."""
from __future__ import annotations

import os
import tempfile

import pytest

from app.modules.selector import load_from_dict, load_from_yaml


def test_load_empty_dict_yields_empty_registry():
    root = load_from_dict({})
    assert root.selectors == {}


def test_load_root_with_no_selectors_key():
    root = load_from_dict({"selectors": None})
    assert root.selectors == {}


def test_load_from_yaml_file():
    yaml_text = """
selectors:
  traders:
    namespace: "sel:y"
    quality_weight: 0.5
    bandit_weight: 0.5
    metrics:
      - name: "x"
        weight: 1.0
        min_value: 0.0
        max_value: 1.0
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        f.write(yaml_text)
        path = f.name
    try:
        root = load_from_yaml(path)
        cfg = root.get("traders")
        assert cfg.namespace == "sel:y"
        assert cfg.quality_weight == 0.5
        assert len(cfg.metrics) == 1
    finally:
        os.unlink(path)


def test_load_from_yaml_empty_file():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        path = f.name
    try:
        root = load_from_yaml(path)
        assert root.selectors == {}
    finally:
        os.unlink(path)


def test_load_from_yaml_non_mapping_root_raises():
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        f.write("- just a list\n- of items\n")
        path = f.name
    try:
        with pytest.raises(ValueError, match="YAML root must be a mapping"):
            load_from_yaml(path)
    finally:
        os.unlink(path)


def test_load_full_metric_options():
    """Every optional metric field must round-trip."""
    root = load_from_dict({
        "selectors": {
            "t": {
                "metrics": [
                    {
                        "name": "m",
                        "weight": 0.5,
                        "min_value": -10.0,
                        "max_value": 10.0,
                        "higher_is_better": False,
                        "min_sample_size": 42,
                        "default_value": 0.5,
                        "max_age_sec": 999,
                    }
                ]
            }
        }
    })
    spec = root.get("t").metrics[0]
    assert spec.name == "m"
    assert spec.min_value == -10.0
    assert spec.max_value == 10.0
    assert spec.higher_is_better is False
    assert spec.min_sample_size == 42
    assert spec.default_value == 0.5
    assert spec.max_age_sec == 999


def test_load_metric_with_none_optionals():
    """default_value and max_age_sec are optional; absent → None on the dataclass."""
    root = load_from_dict({
        "selectors": {
            "t": {
                "metrics": [
                    {
                        "name": "m",
                        "weight": 1.0,
                        "min_value": 0.0,
                        "max_value": 1.0,
                    }
                ]
            }
        }
    })
    spec = root.get("t").metrics[0]
    assert spec.default_value is None
    assert spec.max_age_sec is None


def test_load_experiment_with_empty_variants():
    root = load_from_dict({
        "selectors": {
            "t": {
                "experiments": [{"name": "exp1"}],
            }
        }
    })
    exp = root.get("t").experiments[0]
    assert exp.name == "exp1"
    assert exp.enabled is False
    assert exp.variants == {}


def test_load_circuit_breaker_with_partial_overrides():
    root = load_from_dict({
        "selectors": {
            "t": {
                "circuit_breaker": {"enabled": True, "failure_threshold": 7},
            }
        }
    })
    cb = root.get("t").circuit_breaker
    assert cb.enabled is True
    assert cb.failure_threshold == 7
    # Other fields keep defaults
    assert cb.window_sec == 60
    assert cb.cooldown_sec == 300


def test_load_reward_with_only_timeout():
    root = load_from_dict({
        "selectors": {"t": {"reward": {"timeout_sec": 999}}}
    })
    r = root.get("t").reward
    assert r.timeout_sec == 999
    assert r.accepted == 0.3  # default
    assert r.completed == 0.7  # default


def test_load_cold_start_full():
    root = load_from_dict({
        "selectors": {
            "t": {
                "cold_start": {
                    "forced_exploration_orders": 20,
                    "bootstrap_alpha": 3.0,
                    "bootstrap_beta": 2.0,
                }
            }
        }
    })
    cs = root.get("t").cold_start
    assert cs.forced_exploration_orders == 20
    assert cs.bootstrap_alpha == 3.0
    assert cs.bootstrap_beta == 2.0


def test_root_config_get_missing_raises():
    root = load_from_dict({})
    with pytest.raises(KeyError, match="not configured"):
        root.get("nope")


def test_load_namespace_defaults_to_name_based():
    """Selector with no namespace gets a default keyed off its name."""
    root = load_from_dict({"selectors": {"foo": {}}})
    assert root.get("foo").namespace == "sel:foo"
