"""Celery tasks driving the selector subsystem.

  * ``record_selector_feedback`` — apply a reward signal for one finished order
    (enqueued from OrderService.complete_order / fail_order). This is what
    keeps the bandit learning from real outcomes.
  * ``recompute_selector_metrics_task`` — periodic refresh of business metrics
    from the live order tables into the selector's Redis state.
  * ``sweep_selector_feedback_timeouts_task`` — auto-feedback for decisions
    that never came back with a result.

All tasks are idempotent and safe to run on a tight schedule (e.g. every
minute). The actual aggregator is injected by the application bootstrap; if
no aggregator / no registry is wired (e.g. dev), the tasks no-op.
"""
from __future__ import annotations

import asyncio
import logging
import threading

from app.workers.celery_app import celery_app


logger = logging.getLogger(__name__)


def _run_async(coro):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        result: list = []
        thread = threading.Thread(target=lambda: result.append(asyncio.run(coro)))
        thread.start()
        thread.join()
        return result[0] if result else None
    return asyncio.run(coro)


async def _recompute_all() -> None:
    from app.modules.selector.bootstrap import get_recomputers

    recomputers = get_recomputers()
    if not recomputers:
        logger.info("selector_recompute_skipped_no_jobs_wired")
        return
    for r in recomputers:
        try:
            report = await r.run()
            logger.info(
                "selector_recompute_done",
                extra={
                    "selector": report.selector_name,
                    "entities": report.entities_updated,
                    "metrics": report.metrics_written,
                    "duration_sec": round(report.duration_sec, 3),
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("selector_recompute_failed: %s", exc)


async def _sweep_timeouts() -> None:
    from app.modules.selector.bootstrap import get_timeout_jobs

    jobs = get_timeout_jobs()
    if not jobs:
        return
    for j in jobs:
        try:
            report = await j.sweep()
            if report.swept:
                logger.info(
                    "selector_feedback_timeout_swept",
                    extra={
                        "selector": report.selector_name,
                        "swept": report.swept,
                        "fed_back": report.fed_back,
                    },
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("selector_timeout_sweep_failed: %s", exc)


async def _record_feedback(
    selector_name: str, order_id: str, entity_id: str, signal: str
) -> None:
    from app.modules.selector.bootstrap import get_registry

    registry = get_registry()
    if registry is None or selector_name not in registry.names():
        # Selector not wired (e.g. dev / not enabled) — nothing to learn from.
        return
    sel = registry.get(selector_name)
    await sel.feedback(order_id=order_id, entity_id=entity_id, signal=signal)


@celery_app.task(name="record_selector_feedback", bind=True, max_retries=3)
def record_selector_feedback(
    self,
    *,
    selector_name: str = "traders",
    order_id: str,
    entity_id: str,
    signal: str = "completed",
):
    """Apply one order's outcome to the bandit. Idempotent by ``order_id``
    inside the selector, so retries can't double-count."""
    try:
        _run_async(_record_feedback(selector_name, order_id, entity_id, signal))
    except Exception as e:
        logger.warning("record_selector_feedback_failed: %s", e)
        raise self.retry(exc=e, countdown=2 ** self.request.retries)


@celery_app.task(name="recompute_selector_metrics_task", bind=True, max_retries=3)
def recompute_selector_metrics_task(self):
    try:
        _run_async(_recompute_all())
    except Exception as e:
        logger.error("recompute_selector_metrics_task_failed: %s", e)
        raise self.retry(exc=e, countdown=2 ** self.request.retries)


@celery_app.task(
    name="sweep_selector_feedback_timeouts_task", bind=True, max_retries=3
)
def sweep_selector_feedback_timeouts_task(self):
    try:
        _run_async(_sweep_timeouts())
    except Exception as e:
        logger.error("sweep_selector_feedback_timeouts_task_failed: %s", e)
        raise self.retry(exc=e, countdown=2 ** self.request.retries)
