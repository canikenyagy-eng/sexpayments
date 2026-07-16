import pytest

from app.modules.selector import MetricSpec, SelectorConfig, load_from_dict
from app.modules.selector.config.models import (
    CircuitBreakerConfig,
    FairnessConfig,
)


def test_load_minimal_dict():
    root = load_from_dict({"selectors": {"x": {"namespace": "ns:x"}}})
    cfg = root.get("x")
    assert cfg.name == "x"
    assert cfg.namespace == "ns:x"
    assert cfg.bandit_strategy == "thompson"
    assert cfg.policy == "softmax"


def test_load_full_dict():
    root = load_from_dict({
        "selectors": {
            "t": {
                "namespace": "ns:t",
                "quality_weight": 0.6,
                "bandit_weight": 0.4,
                "metrics": [
                    {
                        "name": "x",
                        "weight": 1.0,
                        "min_value": 0.0,
                        "max_value": 10.0,
                        "higher_is_better": False,
                        "min_sample_size": 10,
                        "default_value": 5.0,
                        "max_age_sec": 3600,
                    }
                ],
                "circuit_breaker": {
                    "enabled": True,
                    "failure_threshold": 3,
                    "window_sec": 30,
                    "cooldown_sec": 120,
                },
                "fairness": {"min_share": 0.05, "max_share": 0.4},
                "experiments": [
                    {
                        "name": "exp1",
                        "enabled": True,
                        "variants": {
                            "control": {"weight": 50, "policy": "random"},
                            "treatment": {"weight": 50},
                        },
                    }
                ],
            }
        }
    })
    cfg = root.get("t")
    assert cfg.metrics[0].max_age_sec == 3600
    assert cfg.circuit_breaker.failure_threshold == 3
    assert cfg.fairness.min_share == 0.05
    assert cfg.experiments[0].variants["control"].policy == "random"


def test_unknown_strategy_rejected():
    with pytest.raises(ValueError, match="unknown bandit_strategy"):
        SelectorConfig(name="x", namespace="ns:x", bandit_strategy="nonsense")


def test_unknown_policy_rejected():
    with pytest.raises(ValueError, match="unknown policy"):
        SelectorConfig(name="x", namespace="ns:x", policy="weighted")


def test_bad_decay_rejected():
    with pytest.raises(ValueError, match="decay_factor"):
        SelectorConfig(name="x", namespace="ns:x", decay_factor=1.5)
    with pytest.raises(ValueError, match="decay_factor"):
        SelectorConfig(name="x", namespace="ns:x", decay_factor=0.0)


def test_bad_weights_rejected():
    with pytest.raises(ValueError, match="quality_weight"):
        SelectorConfig(
            name="x", namespace="ns:x", quality_weight=-0.1
        )
    with pytest.raises(ValueError, match="must be > 0"):
        SelectorConfig(
            name="x",
            namespace="ns:x",
            quality_weight=0.0,
            bandit_weight=0.0,
        )


def test_metric_validation():
    with pytest.raises(ValueError, match="max_value"):
        MetricSpec(name="m", weight=1.0, min_value=10.0, max_value=5.0)
    with pytest.raises(ValueError, match="weight"):
        MetricSpec(name="m", weight=-1.0, min_value=0.0, max_value=1.0)


def test_fairness_validation():
    with pytest.raises(ValueError, match="min_share must be <= max_share"):
        FairnessConfig(min_share=0.5, max_share=0.3)
    with pytest.raises(ValueError, match="min_share"):
        FairnessConfig(min_share=-0.1, max_share=1.0)


def test_circuit_breaker_defaults_disabled():
    cb = CircuitBreakerConfig()
    assert cb.enabled is False
    assert cb.failure_threshold == 5
