"""Readiness checks for the backend's own dependencies (DB / Redis / ClickHouse).

``/health`` stays a shallow **liveness** probe (the process is up — a DB blip must
not get the pod killed). ``/health/ready`` calls :func:`check_readiness`, which
actively pings each dependency and reports per-dependency status, so a load
balancer / orchestrator can drain a node when Postgres is unreachable instead of
routing traffic into a black hole, and a rolling deploy has a real "ready" signal.

Each probe result is also published to Prometheus as
``primepay_dependency_up{dependency=...}`` so alerts can fire on a specific
dependency from the API's own vantage point (complements the exporters).

All checks run concurrently and are individually time-boxed — a hung dependency
degrades to "down" within ``_CHECK_TIMEOUT_S`` rather than blocking the probe.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Dict, Optional, Tuple

from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.observability import set_dependency_up
from app.infrastructure.cache.redis import redis_client
from app.infrastructure.db.session import engine

logger = get_logger(__name__)

_CHECK_TIMEOUT_S = 2.0


async def _check_postgres() -> bool:
    async with engine.connect() as conn:
        await conn.execute(text("SELECT 1"))
    return True


async def _check_redis() -> bool:
    return bool(await redis_client.ping())


async def _check_clickhouse() -> bool:
    from app.infrastructure.clickhouse.client import get_client

    loop = asyncio.get_running_loop()
    return bool(await loop.run_in_executor(None, lambda: get_client().ping()))


async def _run_check(name: str, coro: Awaitable[bool]) -> Tuple[str, bool, Optional[str]]:
    """Time-box a single probe, publish its gauge, and never raise — a probe
    failure IS the signal, not an error."""
    try:
        ok = bool(await asyncio.wait_for(coro, timeout=_CHECK_TIMEOUT_S))
        set_dependency_up(name, ok)
        return name, ok, None
    except Exception as exc:  # noqa: BLE001 — degraded dependency, not a crash
        set_dependency_up(name, False)
        logger.warning("readiness probe failed", dependency=name, error=str(exc))
        return name, False, str(exc)[:200]


async def check_readiness() -> Tuple[bool, Dict[str, Any]]:
    """Probe every enabled dependency concurrently.

    Returns ``(all_ok, {dep: {"up": bool, "error"?: str}})``. ClickHouse is
    probed only when ``CLICKHOUSE_ENABLED`` (it's optional infra — its absence
    must not fail readiness when the platform runs without it).
    """
    settings = get_settings()
    checks = [
        _run_check("postgres", _check_postgres()),
        _run_check("redis", _check_redis()),
    ]
    if settings.CLICKHOUSE_ENABLED:
        checks.append(_run_check("clickhouse", _check_clickhouse()))

    results = await asyncio.gather(*checks)

    deps: Dict[str, Any] = {}
    all_ok = True
    for name, ok, err in results:
        deps[name] = {"up": ok}
        if err is not None:
            deps[name]["error"] = err
        all_ok = all_ok and ok
    return all_ok, deps
