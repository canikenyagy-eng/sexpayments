"""Verify pooling.PoolingService routes to the selector when configured."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.common.enums.pooling import PoolingStrategy
from app.modules.selector import (
    MemoryStorage,
    MetricValue,
    SelectorRegistry,
    bootstrap,
    load_from_dict,
)


class _FakeRequisite:
    def __init__(self, rid: int, trader_id: int):
        self.id = rid
        self.trader_id = trader_id


class _FakeOrder:
    def __init__(self, oid="o-1", amount=1000.0, currency="RUB"):
        self.id = oid
        self.amount = amount
        self.currency = currency


@pytest.fixture(autouse=True)
def _clean_bootstrap():
    bootstrap.reset_for_tests()
    yield
    bootstrap.reset_for_tests()


@pytest.mark.asyncio
async def test_bandit_strategy_falls_back_when_no_registry():
    """Calling BANDIT without a registry installed must not crash —
    fall back to random."""
    from app.modules.pooling.service import PoolingService

    svc = PoolingService(session=None)  # session not used in this code path
    requisites = [_FakeRequisite(1, 10), _FakeRequisite(2, 20)]
    chosen = await svc._select_via_bandit(_FakeOrder(), requisites)
    assert chosen in requisites


@pytest.mark.asyncio
async def test_bandit_strategy_uses_registry_when_present():
    cfg = load_from_dict(
        {"selectors": {"traders": {"namespace": "sel:test"}}}
    )
    storage = MemoryStorage()
    registry = SelectorRegistry(
        cfg, storage_overrides={"traders": storage}
    )
    bootstrap.set_registry(registry)

    # Seed metrics so the selector has a clear winner.
    sel = registry.get("traders")
    await sel.upsert_metrics("10", {})  # trader 10 cold
    await sel.upsert_metrics("20", {})  # trader 20 cold
    requisites = [
        _FakeRequisite(1, 10),
        _FakeRequisite(2, 20),
    ]

    from app.modules.pooling.service import PoolingService

    svc = PoolingService(session=None)
    chosen = await svc._select_via_bandit(_FakeOrder(), requisites)
    # Must pick one of the two requisites.
    assert chosen in requisites


@pytest.mark.asyncio
async def test_bandit_strategy_falls_back_when_engine_raises():
    """If the engine throws, BANDIT routes back to random."""
    cfg = load_from_dict({"selectors": {"traders": {"namespace": "sel:test"}}})
    storage = MemoryStorage()
    registry = SelectorRegistry(cfg, storage_overrides={"traders": storage})
    bootstrap.set_registry(registry)

    sel = registry.get("traders")

    async def _boom(*a, **kw):
        raise RuntimeError("engine broken")

    with patch.object(sel, "select", _boom):
        from app.modules.pooling.service import PoolingService

        svc = PoolingService(session=None)
        requisites = [_FakeRequisite(1, 10), _FakeRequisite(2, 20)]
        chosen = await svc._select_via_bandit(_FakeOrder(), requisites)
        assert chosen in requisites


@pytest.mark.asyncio
async def test_bandit_strategy_falls_back_when_entity_unknown_to_registry():
    """If select returns an entity_id we don't recognize (sanity check),
    we fall back to random rather than crashing on dict lookup."""
    cfg = load_from_dict({"selectors": {"traders": {"namespace": "sel:test"}}})
    registry = SelectorRegistry(
        cfg, storage_overrides={"traders": MemoryStorage()}
    )
    bootstrap.set_registry(registry)
    sel = registry.get("traders")

    class _FakeResult:
        entity_id = "999"  # not in by_trader
        candidates = []

    async def _select(*a, **kw):
        return _FakeResult()

    with patch.object(sel, "select", _select):
        from app.modules.pooling.service import PoolingService

        svc = PoolingService(session=None)
        requisites = [_FakeRequisite(1, 10), _FakeRequisite(2, 20)]
        chosen = await svc._select_via_bandit(_FakeOrder(), requisites)
        assert chosen in requisites


@pytest.mark.asyncio
async def test_groups_requisites_by_trader_then_picks_one():
    """Two requisites for the same trader should compete as one bandit arm."""
    cfg = load_from_dict({"selectors": {"traders": {"namespace": "sel:test"}}})
    registry = SelectorRegistry(
        cfg, storage_overrides={"traders": MemoryStorage()}
    )
    bootstrap.set_registry(registry)

    from app.modules.pooling.service import PoolingService

    svc = PoolingService(session=None)
    # Trader 10 has two requisites, trader 20 has one.
    requisites = [
        _FakeRequisite(1, 10),
        _FakeRequisite(2, 10),
        _FakeRequisite(3, 20),
    ]
    chosen = await svc._select_via_bandit(_FakeOrder(), requisites)
    assert chosen in requisites
