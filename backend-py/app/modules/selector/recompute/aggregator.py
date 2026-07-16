"""Metric aggregator — pluggable data source for the recompute job.

The recompute job needs a way to ask "what's the latest value of metric X
for entity Y?" without knowing whether the data lives in Postgres,
ClickHouse, or some hand-rolled cache. ``MetricAggregator`` is that seam.

For now, the only impl in tree is ``CallableAggregator`` which delegates to
a plain async callable — handy for SQLAlchemy-backed aggregation from the
existing tables. A ``ClickHouseAggregator`` slots in here when CH lands.
"""
from __future__ import annotations

from typing import Awaitable, Callable, Iterable, Protocol

from app.modules.selector.core.stats import MetricValue


class MetricAggregator(Protocol):
    async def aggregate(
        self, entity_ids: Iterable[str], window_sec: int
    ) -> dict[str, dict[str, MetricValue]]:
        """Return {entity_id -> {metric_name -> MetricValue}}.

        Missing entities or metrics simply aren't keys in the result; the
        recompute job leaves their stored value alone in that case.
        """
        ...


class CallableAggregator:
    """Adapter for any async function returning the expected shape.

    Useful for wiring SQL queries without writing a class per source:

        async def fetch(ids, window):
            ...  # SQLAlchemy aggregate
            return {"trader-1": {"conversion": MetricValue(0.7, 240)}}

        recomputer = MetricsRecomputer(registry, "traders", CallableAggregator(fetch))
    """

    def __init__(
        self,
        fn: Callable[
            [Iterable[str], int],
            Awaitable[dict[str, dict[str, MetricValue]]],
        ],
    ):
        self._fn = fn

    async def aggregate(
        self, entity_ids: Iterable[str], window_sec: int
    ) -> dict[str, dict[str, MetricValue]]:
        return await self._fn(entity_ids, window_sec)
