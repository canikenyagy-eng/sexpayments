"""Offline simulator — replay historical events through a fresh selector.

Use case: try a new config (different weights, different metric thresholds,
new bandit strategy) against last week's traffic before pushing to prod.

A trace is just an iterable of ``HistoricalEvent`` records. The simulator
walks through them in order, calls ``select`` and ``feedback`` on a
test-only EntitySelector (backed by MemoryStorage so each run is
hermetic), and reports aggregate quality at the end.

The "ground truth" reward per (order_id, entity_id) pair is supplied by the
trace itself: the historical reward observed in production. When the
simulated policy picks a different entity than prod did, the simulator
either:
  * uses the trace's reward IF the pair was observed (rare); or
  * treats the counterfactual reward as missing and computes an IPS-style
    estimate (see ``counterfactual.py``) at report time.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Iterable, Optional

from app.modules.selector.config.models import SelectorConfig
from app.modules.selector.core.selector import (
    EntitySelector,
    SelectionContext,
    SelectionResult,
)
from app.modules.selector.core.stats import MetricValue
from app.modules.selector.factory import build_memory_selector


@dataclass
class HistoricalEvent:
    order_id: str
    candidates: list[str]
    chosen_entity_id: str
    reward: float
    propensity: float = 0.0
    """Probability of choosing ``chosen_entity_id`` under the production policy.

    Filled in if known (so IPS estimators can use it). When the production
    policy was uniform random over N candidates, this is 1/N; with the
    bandit it's the recorded ``probability`` for that candidate."""

    timestamp: float = 0.0
    metrics: dict[str, dict[str, MetricValue]] = field(default_factory=dict)
    """Optional snapshot of business metrics per entity at event time.

    The simulator calls ``upsert_metrics`` with this before the select,
    so swapping in a config with different metric weights reflects the
    same business reality as prod would have seen."""


@dataclass
class SimulationReport:
    total_events: int
    # Reward earned by the simulated policy on events where it picked the
    # historical entity (i.e. ground-truth reward was observable).
    realised_reward_sum: float
    realised_count: int
    # Events where the simulated policy diverged from production.
    counterfactual_count: int
    # Per-entity selection counts under the new policy.
    selection_counts: dict[str, int]
    # Final α/β snapshot per entity, useful for sanity-checking decay.
    alpha_beta: dict[str, tuple[float, float]]

    @property
    def realised_avg_reward(self) -> float:
        if not self.realised_count:
            return 0.0
        return self.realised_reward_sum / self.realised_count

    @property
    def coverage(self) -> float:
        if not self.total_events:
            return 0.0
        return self.realised_count / self.total_events


class OfflineSimulator:
    def __init__(
        self,
        config: SelectorConfig,
        *,
        rng_seed: int = 0,
    ):
        self._selector: EntitySelector = build_memory_selector(
            config, rng=random.Random(rng_seed)
        )

    @property
    def selector(self) -> EntitySelector:
        return self._selector

    async def replay(
        self, events: Iterable[HistoricalEvent]
    ) -> SimulationReport:
        realised_sum = 0.0
        realised_count = 0
        counterfactual_count = 0
        total = 0
        selection_counts: dict[str, int] = {}

        for ev in events:
            total += 1
            # 1. Hydrate metrics so the new policy reflects what prod saw.
            for eid, metrics in ev.metrics.items():
                if metrics:
                    await self._selector.upsert_metrics(eid, metrics)
            # 2. Run the policy on this event.
            ctx = SelectionContext(order_id=ev.order_id)
            result: SelectionResult = await self._selector.select(
                ev.candidates, ctx
            )
            chosen = result.entity_id
            if chosen is None:
                continue
            selection_counts[chosen] = selection_counts.get(chosen, 0) + 1

            # 3. Compare against production's pick.
            if chosen == ev.chosen_entity_id:
                # Ground-truth reward is directly observed.
                realised_sum += ev.reward
                realised_count += 1
                await self._selector.feedback(
                    order_id=ev.order_id,
                    entity_id=chosen,
                    reward=ev.reward,
                )
            else:
                # Counterfactual — we don't know what reward this entity
                # would have produced. Don't fabricate one; skip the update.
                counterfactual_count += 1

        alpha_beta: dict[str, tuple[float, float]] = {}
        for eid in await self._selector._storage.list_entity_ids():
            stats = await self._selector._storage.get(eid)
            if stats is not None:
                alpha_beta[eid] = (stats.alpha, stats.beta)

        return SimulationReport(
            total_events=total,
            realised_reward_sum=realised_sum,
            realised_count=realised_count,
            counterfactual_count=counterfactual_count,
            selection_counts=selection_counts,
            alpha_beta=alpha_beta,
        )
