"""Final mop-up: cover every remaining branch the other test files miss."""
from __future__ import annotations

import asyncio
import sys
import time

import pytest

from app.modules.selector import (
    MemoryStorage,
    SelectorConfig,
    SelectorRegistry,
    load_from_dict,
    load_from_yaml,
)
from app.modules.selector.config.models import (
    FairnessConfig,
    MetricSpec,
    SelectorConfig as SC,
)
from app.modules.selector.core.bandit import _decay_factor
from app.modules.selector.core.policy import _weighted_choice
from app.modules.selector.core.selector import _empty_result
from app.modules.selector.core.stats import EntityStats
from app.modules.selector.hooks.fairness import _cap_max


# --- loader: PyYAML missing ----------------------------------------------


def test_load_from_yaml_raises_when_pyyaml_missing(monkeypatch, tmp_path):
    """If PyYAML is not installed, load_from_yaml gives a clear runtime error."""
    monkeypatch.setitem(sys.modules, "yaml", None)
    path = tmp_path / "x.yaml"
    path.write_text("selectors: {}")
    with pytest.raises(RuntimeError, match="PyYAML"):
        load_from_yaml(str(path))


# --- models: validation branches ----------------------------------------


def test_max_share_above_one_rejected():
    with pytest.raises(ValueError, match="max_share"):
        FairnessConfig(min_share=0.0, max_share=1.5)


def test_decay_interval_zero_rejected():
    with pytest.raises(ValueError, match="decay_interval_sec"):
        SC(name="t", namespace="ns:t", decay_interval_sec=0)


def test_softmax_temperature_zero_rejected():
    with pytest.raises(ValueError, match="softmax_temperature"):
        SC(name="t", namespace="ns:t", softmax_temperature=0.0)


def test_min_exploration_above_one_rejected():
    with pytest.raises(ValueError, match="min_exploration_prob"):
        SC(name="t", namespace="ns:t", min_exploration_prob=1.5)


def test_min_exploration_negative_rejected():
    with pytest.raises(ValueError, match="min_exploration_prob"):
        SC(name="t", namespace="ns:t", min_exploration_prob=-0.1)


def test_metric_by_name_finds_existing():
    cfg = SC(
        name="t",
        namespace="ns:t",
        metrics=(
            MetricSpec(name="a", weight=1.0, min_value=0.0, max_value=1.0),
            MetricSpec(name="b", weight=1.0, min_value=0.0, max_value=1.0),
        ),
    )
    assert cfg.metric_by_name("a").name == "a"
    assert cfg.metric_by_name("b").name == "b"


def test_metric_by_name_missing_returns_none():
    cfg = SC(
        name="t",
        namespace="ns:t",
        metrics=(MetricSpec(name="a", weight=1.0, min_value=0.0, max_value=1.0),),
    )
    assert cfg.metric_by_name("nope") is None


def test_metric_by_name_no_metrics():
    cfg = SC(name="t", namespace="ns:t", metrics=())
    assert cfg.metric_by_name("x") is None


# --- bandit: edge cases --------------------------------------------------


def test_decay_factor_zero_elapsed_returns_one():
    cfg = SC(name="t", namespace="ns:t", decay_factor=0.5, decay_interval_sec=60)
    assert _decay_factor(cfg, 0.0) == 1.0


def test_ucb_with_zero_sample_count_returns_max():
    """An arm with n <= 0 (which shouldn't normally happen) returns 1.0
    so it isn't accidentally starved."""
    from app.modules.selector.core.bandit import UCBBandit

    bandit = UCBBandit(SC(name="t", namespace="ns:t"))
    # Manually craft a degenerate stats record where alpha+beta-1 <= 0.
    s = EntityStats(entity_id="e", alpha=0.5, beta=0.4, last_updated=0.0)
    val = bandit.sample(s, now=0.0)
    assert val == 1.0


# --- policy: degenerate distributions -----------------------------------


def test_softmax_zero_total_falls_back_uniform(monkeypatch):
    """If exp() somehow zeros out (numerically degenerate), the defensive
    branch hands back a uniform distribution rather than dividing by zero."""
    import app.modules.selector.core.policy as policy_mod

    # Force every exp() to return 0 to hit the total<=0 branch.
    monkeypatch.setattr(policy_mod.math, "exp", lambda x: 0.0)
    p = policy_mod.SoftmaxPolicy(SC(name="t", namespace="ns:t"))
    probs = p.probabilities([0.1, 0.2, 0.3])
    assert probs == [1 / 3, 1 / 3, 1 / 3]


def test_apply_floor_renormalize_handles_zero_total():
    from app.modules.selector.core.policy import _apply_floor_and_renormalize

    # When even after applying the floor everyone is zero (floor=0), the
    # branch returns the input unchanged via the "floor <= 0" short-circuit.
    # To hit the "total <= 0 after lift" branch we need a positive floor and
    # an input that ends up zero — which means the input was empty.
    # Empty list:
    assert _apply_floor_and_renormalize([], 0.5) == []


def test_apply_floor_zero_total_after_lift_returns_uniform():
    """Defensive branch: if sum() somehow returns 0 after the lift, fall
    back to a uniform distribution instead of dividing by zero."""
    from unittest.mock import patch

    import app.modules.selector.core.policy as policy_mod

    with patch.object(policy_mod, "sum", create=True, return_value=0.0):
        out = policy_mod._apply_floor_and_renormalize([0.5, 0.5], floor=0.1)
    assert out == [0.5, 0.5]


def test_weighted_choice_floating_point_slack_falls_through():
    """If cumulative sum is just under 1 due to FP error and the RNG picks
    a value above it, _weighted_choice returns the last index."""
    class _Pinned:
        def random(self):
            return 0.9999999999

    # Probs sum to just under 1.0.
    probs = [0.3, 0.3, 0.3]  # sum = 0.9
    idx = _weighted_choice(probs, _Pinned())
    assert idx == 2  # falls through to last_idx


@pytest.mark.asyncio
async def test_selector_sample_index_floating_point_fallback(monkeypatch):
    """Same fallback inside the engine's private _sample_index helper."""
    import app.modules.selector.core.selector as selector_mod

    class _Pinned:
        def random(self):
            return 0.9999999999

    # Probs deliberately sum to less than 1.
    idx = selector_mod._sample_index([0.3, 0.3, 0.3], _Pinned())
    assert idx == 2


# --- selector: _empty_result -------------------------------------------


def test_empty_result_helper():
    t0 = time.perf_counter()
    r = _empty_result("no_candidates", considered=5, t0=t0)
    assert r.entity_id is None
    assert r.reason == "no_candidates"
    assert r.candidates_considered == 5
    assert r.candidates_after_filters == 0
    assert r.latency_ms >= 0


# --- fairness: degenerate --------------------------------------------------


def test_cap_max_with_only_capped_returns_capped():
    """All arms at or above cap and no under-cap room → caller gets the
    capped vector verbatim (no renormalization done)."""
    out = _cap_max([0.7, 0.6], cap=0.5)
    # Both above cap → no under-cap room → return [0.5, 0.5] unrenormalized
    assert out == [0.5, 0.5]


def test_cap_max_zero_total_returns_uniform():
    """Defensive: if normalization sum lands at 0, hand back uniform."""
    from unittest.mock import patch

    import app.modules.selector.hooks.fairness as fairness_mod

    with patch.object(fairness_mod, "sum", create=True, return_value=0.0):
        out = fairness_mod._cap_max([0.9, 0.05, 0.05], cap=0.5)
    assert out == [1 / 3, 1 / 3, 1 / 3]


# --- memory storage: expiry + missing ----------------------------------


@pytest.mark.asyncio
async def test_feedback_token_expires():
    storage = MemoryStorage()
    # Claim with TTL=0 → expires immediately.
    assert await storage.claim_feedback_token("o1", ttl_sec=0) is True
    # Force an expiry by advancing time inside the storage (we can do this
    # by directly tweaking the internal map's expiry).
    storage._feedback_tokens["o1"] = time.time() - 1.0
    # Now the next claim should sweep the expired entry and re-claim.
    assert await storage.claim_feedback_token("o1", ttl_sec=60) is True


@pytest.mark.asyncio
async def test_increment_selection_counter_missing_entity_returns_zero():
    storage = MemoryStorage()
    assert await storage.increment_selection_counter("ghost") == 0


@pytest.mark.asyncio
async def test_list_entity_ids_returns_all_stored():
    storage = MemoryStorage()
    from app.modules.selector import new_stats

    for eid in ["a", "b", "c"]:
        await storage.save(new_stats(eid))
    assert set(await storage.list_entity_ids()) == {"a", "b", "c"}


# --- registry: config property ----------------------------------------


def test_registry_exposes_current_config():
    cfg = load_from_dict({"selectors": {"t": {}}})
    reg = SelectorRegistry(cfg, storage_overrides={"t": MemoryStorage()})
    assert reg.config is cfg


def test_registry_config_updates_after_reload():
    cfg1 = load_from_dict({"selectors": {"t": {}}})
    reg = SelectorRegistry(cfg1, storage_overrides={"t": MemoryStorage()})
    cfg2 = load_from_dict({"selectors": {"t": {"namespace": "ns:other"}}})
    reg.reload(cfg2)
    assert reg.config is cfg2
    assert reg.get("t").config.namespace == "ns:other"


# --- circuit breaker: more error paths ---------------------------------


@pytest.mark.asyncio
async def test_cb_zcount_error_in_maybe_trip_swallows():
    """If zcount fails during _maybe_trip, the breaker should not propagate."""
    from app.modules.selector.config.models import CircuitBreakerConfig
    from app.modules.selector.hooks.circuit_breaker import CircuitBreaker

    class _PartiallyExplodingRedis:
        """pipeline().execute() succeeds (so record completes), but zcount blows up."""

        def pipeline(self):
            class _P:
                def __getattr__(self, name):
                    return lambda *a, **kw: self

                async def execute(self_inner):
                    return []

            return _P()

        async def zcount(self, *a, **kw):
            raise RuntimeError("boom")

    cb = CircuitBreaker(
        _PartiallyExplodingRedis(),
        namespace="sel:test",
        config=CircuitBreakerConfig(enabled=True, failure_threshold=1),
    )
    # _maybe_trip should swallow the zcount error.
    await cb.record("e", success=False)


@pytest.mark.asyncio
async def test_cb_set_error_in_maybe_trip_swallows():
    """If SET fails after a successful zcount, the trip just no-ops."""
    from app.modules.selector.config.models import CircuitBreakerConfig
    from app.modules.selector.hooks.circuit_breaker import CircuitBreaker

    class _SetExplodingRedis:
        def pipeline(self):
            class _P:
                def __getattr__(self, name):
                    return lambda *a, **kw: self

                async def execute(self_inner):
                    return []

            return _P()

        async def zcount(self, *a, **kw):
            return 99  # over threshold

        async def set(self, *a, **kw):
            raise RuntimeError("boom")

    cb = CircuitBreaker(
        _SetExplodingRedis(),
        namespace="sel:test",
        config=CircuitBreakerConfig(enabled=True, failure_threshold=1),
    )
    # Should swallow the set error.
    await cb.record("e", success=False)
