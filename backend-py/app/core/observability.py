"""Prometheus instrumentation for the FastAPI app.

Exposes `/metrics` with the standard RED metrics:
    - http_requests_total{handler, method, status} — Rate + Errors
    - http_request_duration_seconds_bucket{handler, method, le} — Duration

Adds one PrimePay-specific dimension so dashboards can slice per API surface
(admin/trader/merchant/bot), plus a side-channel counter for unmatched routes
so noisy 404s from scanners do not bury legitimate 4xx traffic.

`/metrics` is mounted unauthenticated and is meant to be reached only from
inside the Docker network. The public nginx must not proxy it. Metric labels
use the FastAPI route template (never raw paths with IDs), so cardinality
stays bounded.
"""
from __future__ import annotations

from typing import Callable

from fastapi import FastAPI
from prometheus_client import Counter, Gauge
from prometheus_fastapi_instrumentator import Instrumentator, metrics
from prometheus_fastapi_instrumentator.metrics import Info


_API_SURFACE_PREFIXES = (
    ("/api/merchant/", "merchant"),
    ("/api/bot/", "bot"),
    ("/api/cascade/", "cascade"),
    ("/api/v1/", "internal"),  # admin + trader; split further by handler label
    ("/health", "health"),
)


def _api_surface_for(path: str) -> str:
    for prefix, name in _API_SURFACE_PREFIXES:
        if path.startswith(prefix):
            return name
    return "other"


_http_requests_by_surface = Counter(
    "http_requests_by_surface_total",
    "HTTP requests partitioned by API surface (admin/trader/merchant/bot)",
    labelnames=("method", "api_surface", "status"),
)

_http_unmatched_route = Counter(
    "http_unmatched_route_total",
    "HTTP requests that did not match any FastAPI route",
    labelnames=("method", "status"),
)

_dependency_up = Gauge(
    "primepay_dependency_up",
    "Backend dependency reachable from the API itself (1=up, 0=down)",
    labelnames=("dependency",),
)


def set_dependency_up(dependency: str, up: bool) -> None:
    """Publish the latest readiness-probe result for ``dependency``."""
    _dependency_up.labels(dependency=dependency).set(1 if up else 0)


def _api_surface_label() -> Callable[[Info], None]:
    def instrumentation(info: Info) -> None:
        if info.response is None:
            return
        status = str(info.response.status_code)
        _http_requests_by_surface.labels(
            method=info.request.method,
            api_surface=_api_surface_for(info.request.url.path),
            status=status,
        ).inc()
        if info.modified_handler == "none":
            _http_unmatched_route.labels(method=info.request.method, status=status).inc()

    return instrumentation


_instrumented = False


def setup_metrics(app: FastAPI) -> None:
    """Wire Prometheus metrics into the FastAPI app.

    Must be called once at app construction. Idempotent across reloads.
    """
    global _instrumented
    if _instrumented:
        return
    _instrumented = True

    instrumentator = (
        Instrumentator(
            should_group_status_codes=False,
            should_ignore_untemplated=False,
            should_instrument_requests_inprogress=True,
            inprogress_name="http_requests_inprogress",
            inprogress_labels=True,
            excluded_handlers=["/metrics"],
        )
        .add(metrics.default())
        .add(_api_surface_label())
    )

    instrumentator.instrument(app).expose(
        app,
        endpoint="/metrics",
        include_in_schema=False,
        should_gzip=True,
        tags=["observability"],
    )
