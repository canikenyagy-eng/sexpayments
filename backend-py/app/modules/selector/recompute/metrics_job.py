"""Pull aggregated business metrics into the selector via ``upsert_metrics``.

Run periodically (Celery beat / cron). Reads from any ``MetricAggregator``,
so the data source can be Postgres, ClickHouse, or a precomputed cache —
the recompute job doesn't care.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Iterable, Optional

from app.modules.selector import metrics as prom
from app.modules.selector.core.selector import EntitySelector
from app.modules.selector.recompute.aggregator import MetricAggregator


logger = logging.getLogger(__name__)


@dataclass
class RecomputeReport:
    selector_name: str
    entities_updated: int
    metrics_written: int
    duration_sec: float


class MetricsRecomputer:
    def __init__(
        self,
        selector: EntitySelector,
        aggregator: MetricAggregator,
        *,
        window_sec: int = 3600,
        entity_resolver: Optional[
            "callable[[], Iterable[str]] | callable[[], object]"
        ] = None,
    ):
        self._selector = selector
        self._aggregator = aggregator
        self._window = window_sec
        self._resolver = entity_resolver

    async def _resolve_entity_ids(self) -> list[str]:
        """Pick the set of entities to refresh.

        Order of resolution:
          1. explicit ``entity_resolver`` (caller decides — e.g. all active traders)
          2. fall back to ``storage.list_entity_ids()`` (everyone we already know)
        """
        if self._resolver is not None:
            result = self._resolver()
            if hasattr(result, "__await__"):
                result = await result  # type: ignore[assignment]
            return list(result)  # type: ignore[arg-type]
        return await self._selector._storage.list_entity_ids()

    async def run(self) -> RecomputeReport:
        t0 = time.perf_counter()
        ids = await self._resolve_entity_ids()
        if not ids:
            return RecomputeReport(
                selector_name=self._selector.name,
                entities_updated=0,
                metrics_written=0,
                duration_sec=time.perf_counter() - t0,
            )

        try:
            aggregates = await self._aggregator.aggregate(ids, self._window)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "selector_recompute_aggregate_failed",
                extra={"selector": self._selector.name, "error": str(exc)},
            )
            prom.fallback_total.labels(
                name=self._selector.name, reason="recompute_aggregate"
            ).inc()
            return RecomputeReport(
                selector_name=self._selector.name,
                entities_updated=0,
                metrics_written=0,
                duration_sec=time.perf_counter() - t0,
            )

        written = 0
        for eid, metrics in aggregates.items():
            if not metrics:
                continue
            try:
                await self._selector.upsert_metrics(eid, metrics)
                written += len(metrics)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "selector_recompute_upsert_failed",
                    extra={
                        "selector": self._selector.name,
                        "entity_id": eid,
                        "error": str(exc),
                    },
                )

        # Refresh the active-entities gauge as a side effect: any
        # recompute pass is a good moment to do it.
        try:
            all_ids = await self._selector._storage.list_entity_ids()
            enabled = 0
            for chunk_start in range(0, len(all_ids), 100):
                chunk = all_ids[chunk_start : chunk_start + 100]
                got = await self._selector._storage.get_many(chunk)
                enabled += sum(1 for s in got.values() if s.enabled)
            prom.active_entities.labels(name=self._selector.name).set(enabled)
        except Exception as exc:  # noqa: BLE001
            logger.debug("selector_active_entities_refresh_failed: %s", exc)

        return RecomputeReport(
            selector_name=self._selector.name,
            entities_updated=len(aggregates),
            metrics_written=written,
            duration_sec=time.perf_counter() - t0,
        )
