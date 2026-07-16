"""A/B variant assignment + Redis-backed stickiness."""
from __future__ import annotations

import fakeredis.aioredis
import pytest

from app.modules.selector.config.models import ExperimentConfig, ExperimentVariant
from app.modules.selector.experiments.ab import (
    ABRouter,
    RedisStickyStore,
    assign_variant,
)


def _exp(name="exp1", enabled=True, **variants_kw):
    variants = {
        k: (v if isinstance(v, ExperimentVariant) else ExperimentVariant(**v))
        for k, v in variants_kw.items()
    }
    return ExperimentConfig(name=name, enabled=enabled, variants=variants)


def test_assign_variant_is_deterministic():
    exp = _exp(
        control={"weight": 50, "policy": "random"},
        treatment={"weight": 50, "policy": None},
    )
    n1, v1 = assign_variant(exp, "order-123")
    n2, v2 = assign_variant(exp, "order-123")
    assert n1 == n2
    assert v1 is v2


def test_assign_variant_respects_weights():
    """A 90/10 split should land most orders in the heavy arm."""
    exp = _exp(
        heavy={"weight": 90},
        light={"weight": 10},
    )
    counts = {"heavy": 0, "light": 0}
    for i in range(2000):
        name, _ = assign_variant(exp, f"order-{i}")
        counts[name] += 1
    assert counts["heavy"] > counts["light"] * 5


def test_assign_variant_empty_variants_raises():
    exp = ExperimentConfig(name="empty", enabled=True, variants={})
    with pytest.raises(ValueError, match="no variants"):
        assign_variant(exp, "x")


def test_assign_variant_zero_total_weight_picks_first():
    exp = _exp(
        a={"weight": 0},
        b={"weight": 0},
    )
    name, _ = assign_variant(exp, "order")
    assert name == "a"


def test_router_disabled_when_no_experiments():
    router = ABRouter(experiments=())
    assert router.enabled is False


def test_router_disabled_when_experiment_disabled():
    exp = _exp(enabled=False, control={"weight": 1}, treatment={"weight": 1})
    router = ABRouter(experiments=(exp,))
    assert router.enabled is False


@pytest.mark.asyncio
async def test_router_returns_none_without_order_id():
    exp = _exp(control={"weight": 1}, treatment={"weight": 1})
    router = ABRouter(experiments=(exp,))
    assert await router.assign("") is None


@pytest.mark.asyncio
async def test_router_assignment_carries_policy_override():
    exp = _exp(
        control={"weight": 100, "policy": "random"},
        treatment={"weight": 0},
    )
    router = ABRouter(experiments=(exp,))
    assignment = await router.assign("o1")
    assert assignment is not None
    assert assignment.variant == "control"
    assert assignment.policy_override == "random"


@pytest.mark.asyncio
async def test_router_uses_sticky_store_on_repeated_calls():
    """If sticky store has an entry, reuse it; else assign + persist."""
    exp = _exp(
        control={"weight": 50, "policy": "random"},
        treatment={"weight": 50},
    )
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    sticky = RedisStickyStore(fake)
    router = ABRouter(experiments=(exp,), sticky=sticky, sticky_namespace="sel:t")

    first = await router.assign("order-stable")
    assert first is not None
    # Re-issue — must come from sticky (same variant guaranteed even if hash flipped).
    second = await router.assign("order-stable")
    assert first.variant == second.variant


@pytest.mark.asyncio
async def test_router_ignores_stale_sticky_when_variant_no_longer_exists():
    """If the sticky bucket points to a variant that's been removed from
    config, the router falls back to re-assignment."""
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await fake.set("sel:t:exp:order-stale", "removed_variant")
    exp = _exp(
        control={"weight": 50},
        treatment={"weight": 50},
    )
    router = ABRouter(
        experiments=(exp,),
        sticky=RedisStickyStore(fake),
        sticky_namespace="sel:t",
    )
    assignment = await router.assign("order-stale")
    assert assignment is not None
    assert assignment.variant in ("control", "treatment")


@pytest.mark.asyncio
async def test_sticky_get_swallows_redis_errors():
    class _BoomRedis:
        async def get(self, *a, **kw):
            raise RuntimeError("boom")

        async def set(self, *a, **kw):
            return None

    sticky = RedisStickyStore(_BoomRedis())
    assert await sticky.get("ns", "o") is None


@pytest.mark.asyncio
async def test_sticky_set_swallows_redis_errors():
    class _BoomRedis:
        async def set(self, *a, **kw):
            raise RuntimeError("boom")

    sticky = RedisStickyStore(_BoomRedis())
    await sticky.set("ns", "o", "v", 60)  # must not raise
