"""Tests for the deep readiness check (app.core.health.check_readiness).

The endpoint /health/ready is a thin wrapper that maps (all_ok, deps) → 200/503;
the value lives in the aggregation + per-dependency timeout/gauge logic here.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core import health


@pytest.mark.asyncio
async def test_check_readiness_all_up():
    with patch.object(health, "_check_postgres", AsyncMock(return_value=True)), \
         patch.object(health, "_check_redis", AsyncMock(return_value=True)), \
         patch.object(health, "get_settings", return_value=MagicMock(CLICKHOUSE_ENABLED=False)):
        ok, deps = await health.check_readiness()

    assert ok is True
    assert deps == {"postgres": {"up": True}, "redis": {"up": True}}


@pytest.mark.asyncio
async def test_check_readiness_postgres_down_is_degraded():
    with patch.object(health, "_check_postgres", AsyncMock(side_effect=RuntimeError("no conn"))), \
         patch.object(health, "_check_redis", AsyncMock(return_value=True)), \
         patch.object(health, "get_settings", return_value=MagicMock(CLICKHOUSE_ENABLED=False)):
        ok, deps = await health.check_readiness()

    assert ok is False
    assert deps["postgres"]["up"] is False
    assert "no conn" in deps["postgres"]["error"]
    assert deps["redis"]["up"] is True  # redis stays up — one bad dep degrades the whole


@pytest.mark.asyncio
async def test_check_readiness_includes_clickhouse_when_enabled():
    with patch.object(health, "_check_postgres", AsyncMock(return_value=True)), \
         patch.object(health, "_check_redis", AsyncMock(return_value=True)), \
         patch.object(health, "_check_clickhouse", AsyncMock(return_value=True)), \
         patch.object(health, "get_settings", return_value=MagicMock(CLICKHOUSE_ENABLED=True)):
        ok, deps = await health.check_readiness()

    assert ok is True
    assert deps["clickhouse"] == {"up": True}


@pytest.mark.asyncio
async def test_check_readiness_skips_clickhouse_when_disabled():
    ch = AsyncMock(return_value=True)
    with patch.object(health, "_check_postgres", AsyncMock(return_value=True)), \
         patch.object(health, "_check_redis", AsyncMock(return_value=True)), \
         patch.object(health, "_check_clickhouse", ch), \
         patch.object(health, "get_settings", return_value=MagicMock(CLICKHOUSE_ENABLED=False)):
        ok, deps = await health.check_readiness()

    assert "clickhouse" not in deps  # optional infra — absence must not fail readiness
    ch.assert_not_called()


@pytest.mark.asyncio
async def test_run_check_times_out_to_down(monkeypatch):
    """A probe that hangs past the deadline degrades to down — never blocks."""
    async def _hang() -> bool:
        await asyncio.sleep(10)
        return True

    monkeypatch.setattr(health, "_CHECK_TIMEOUT_S", 0.05)
    with patch.object(health, "set_dependency_up") as gauge:
        name, ok, err = await health._run_check("postgres", _hang())

    assert (name, ok) == ("postgres", False)
    assert err is not None
    gauge.assert_called_once_with("postgres", False)


@pytest.mark.asyncio
async def test_run_check_publishes_gauge_on_success():
    async def _ok() -> bool:
        return True

    with patch.object(health, "set_dependency_up") as gauge:
        name, ok, err = await health._run_check("redis", _ok())

    assert (name, ok, err) == ("redis", True, None)
    gauge.assert_called_once_with("redis", True)


# ── endpoint contract: the 200/503 mapping the load balancer keys on ─────────


@pytest.mark.asyncio
async def test_health_ready_endpoint_200_when_all_up():
    import json

    from app import main

    with patch.object(main, "check_readiness",
                      AsyncMock(return_value=(True, {"postgres": {"up": True}, "redis": {"up": True}}))):
        resp = await main.health_ready()

    assert resp.status_code == 200
    body = json.loads(resp.body)
    assert body["status"] == "ready"
    assert body["dependencies"]["postgres"]["up"] is True


@pytest.mark.asyncio
async def test_health_ready_endpoint_503_when_degraded():
    import json

    from app import main

    with patch.object(main, "check_readiness",
                      AsyncMock(return_value=(False, {"postgres": {"up": False, "error": "no conn"}}))):
        resp = await main.health_ready()

    assert resp.status_code == 503  # the contract the orchestrator drains on
    body = json.loads(resp.body)
    assert body["status"] == "degraded"
    assert body["dependencies"]["postgres"]["up"] is False
