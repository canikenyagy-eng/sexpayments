"""Process-global wiring for the selector subsystem.

The app constructs a single ``SelectorRegistry`` at startup and parks it
here. Endpoints and Celery tasks pull it back out via the accessor
functions. The wiring is optional — if nothing was registered, getters
return ``None`` / empty lists and the rest of the app keeps working.

Why a module-global: the registry holds connection-pinned state (Redis
client, configured experiments) that we don't want to rebuild per request,
and FastAPI dependency injection doesn't have a clean way to live across
both HTTP and Celery workers without a shared module.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.modules.selector.recompute.feedback_timeout import FeedbackTimeoutJob
from app.modules.selector.recompute.metrics_job import MetricsRecomputer
from app.modules.selector.registry import SelectorRegistry


logger = logging.getLogger(__name__)


_registry: Optional[SelectorRegistry] = None
_recomputers: list[MetricsRecomputer] = []
_timeout_jobs: list[FeedbackTimeoutJob] = []


def set_registry(registry: SelectorRegistry) -> None:
    global _registry
    _registry = registry
    logger.info("selector_registry_installed", extra={"selectors": registry.names()})


def get_registry() -> Optional[SelectorRegistry]:
    return _registry


def require_registry() -> SelectorRegistry:
    if _registry is None:
        raise RuntimeError(
            "SelectorRegistry has not been initialised — call "
            "app.modules.selector.bootstrap.set_registry() during app startup"
        )
    return _registry


def register_recomputer(job: MetricsRecomputer) -> None:
    _recomputers.append(job)


def get_recomputers() -> list[MetricsRecomputer]:
    return list(_recomputers)


def register_timeout_job(job: FeedbackTimeoutJob) -> None:
    _timeout_jobs.append(job)


def get_timeout_jobs() -> list[FeedbackTimeoutJob]:
    return list(_timeout_jobs)


def reset_for_tests() -> None:
    """Clear all module state. Tests call this in fixture teardown."""
    global _registry, _recomputers, _timeout_jobs
    _registry = None
    _recomputers = []
    _timeout_jobs = []
