"""EntitySelector — the per-selector engine.

Wires together:
  ConfigLoader -> SelectorConfig -> {QualityScorer, BanditStrategy, SelectionPolicy}
                                 -> Storage
                                 -> CircuitBreaker (optional)
                                 -> ABRouter (optional)
                                 -> EventSink (optional)

Public surface:
  - select(candidate_ids, context) -> SelectionResult
  - feedback(order_id, entity_id, reward|signal) -> bool
  - upsert_metrics(entity_id, metrics) -> None
  - enable(entity_id) / disable(entity_id)
  - explain(entity_id) -> ExplanationReport
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

from app.modules.selector import metrics as m
from app.modules.selector.config.models import SelectorConfig
from app.modules.selector.core.bandit import BanditStrategy, build_bandit
from app.modules.selector.core.policy import SelectionPolicy, build_policy
from app.modules.selector.core.scoring import QualityScorer, ScoreBreakdown
from app.modules.selector.core.stats import EntityStats, MetricValue, new_stats
from app.modules.selector.hooks.circuit_breaker import CircuitBreaker
from app.modules.selector.hooks.fairness import apply_fairness
from app.modules.selector.hooks.segment_filter import filter_by_tags
from app.modules.selector.logging_.events import DecisionEvent, FeedbackEvent
from app.modules.selector.logging_.sinks import EventSink, NullSink
from app.modules.selector.storage.protocol import SelectorStorage

if TYPE_CHECKING:
    # Avoid a circular import — experiments/simulator.py imports EntitySelector.
    # ABRouter is referenced only through method calls (self._ab.assign), and
    # VariantAssignment is duck-typed at the call site.
    from app.modules.selector.experiments.ab import ABRouter, VariantAssignment


logger = logging.getLogger(__name__)


@dataclass
class CandidateScore:
    entity_id: str
    quality: float
    bandit_sample: float
    final_score: float
    probability: float


@dataclass
class SelectionResult:
    entity_id: Optional[str]
    reason: str
    candidates_considered: int
    candidates_after_filters: int
    latency_ms: float
    candidates: list[CandidateScore] = field(default_factory=list)
    experiment_variant: Optional[str] = None


@dataclass
class ExplanationReport:
    entity_id: str
    stats: Optional[EntityStats]
    quality_score: Optional[float]
    metric_breakdown: list[ScoreBreakdown]
    bandit_mean: Optional[float]
    circuit_breaker: Optional[dict]


@dataclass
class SelectionContext:
    order_id: str = ""
    amount: Optional[float] = None
    currency: Optional[str] = None
    user_segment: Optional[str] = None
    require_tags: Optional[dict[str, str]] = None
    extra: dict = field(default_factory=dict)


class EntitySelector:
    def __init__(
        self,
        config: SelectorConfig,
        storage: SelectorStorage,
        *,
        circuit_breaker: Optional[CircuitBreaker] = None,
        ab_router: Optional[ABRouter] = None,
        event_sink: Optional[EventSink] = None,
        rng: Optional[random.Random] = None,
    ):
        self._config = config
        self._storage = storage
        self._rng = rng or random.Random()
        self._scorer = QualityScorer(config)
        self._bandit: BanditStrategy = build_bandit(config, rng=self._rng)
        self._policy: SelectionPolicy = build_policy(config, rng=self._rng)
        self._cb = circuit_breaker
        self._ab = ab_router
        self._sink: EventSink = event_sink or NullSink()

    # --- public properties --------------------------------------------------

    @property
    def config(self) -> SelectorConfig:
        return self._config

    @property
    def name(self) -> str:
        return self._config.name

    # --- public API ---------------------------------------------------------

    async def select(
        self,
        candidate_ids: list[str],
        context: Optional[SelectionContext] = None,
    ) -> SelectionResult:
        t0 = time.perf_counter()
        ctx = context or SelectionContext(order_id="")
        considered = len(candidate_ids)

        if not self._config.enabled:
            result = _empty_result("selector_disabled", considered, t0)
            await self._record_select(ctx, result, t0)
            return result

        if not candidate_ids:
            result = _empty_result("no_candidates", considered, t0)
            await self._record_select(ctx, result, t0)
            return result

        # 1. Load stats batch.
        stats_map = await self._storage.get_many(candidate_ids)

        # 2. Materialise missing entities with cold-start priors.
        materialised: list[EntityStats] = []
        for eid in candidate_ids:
            s = stats_map.get(eid)
            if s is None:
                s = new_stats(
                    eid,
                    alpha=self._config.cold_start.bootstrap_alpha,
                    beta=self._config.cold_start.bootstrap_beta,
                )
            materialised.append(s)

        # 3. Filter: disabled, circuit-broken, segment-mismatched.
        candidates = [s for s in materialised if s.enabled]
        if self._cb and self._cb.enabled:
            open_ids = await self._cb.filter_open([s.entity_id for s in candidates])
            candidates = [s for s in candidates if s.entity_id not in open_ids]
        candidates = filter_by_tags(candidates, ctx.require_tags)

        if not candidates:
            result = _empty_result("all_filtered", considered, t0)
            await self._record_select(ctx, result, t0)
            return result

        # 4. A/B variant assignment (optional).
        variant: Optional[VariantAssignment] = None
        if self._ab is not None and self._ab.enabled:
            variant = await self._ab.assign(ctx.order_id)
        policy = self._policy
        if variant is not None and variant.policy_override is not None:
            policy = build_policy(
                self._config, rng=self._rng, override=variant.policy_override
            )

        # 5. Cold start: forced exploration on any candidate below threshold.
        forced = self._config.cold_start.forced_exploration_orders
        if forced > 0:
            under = [c for c in candidates if c.total_selections < forced]
            if under:
                picked = self._rng.choice(under)
                result = await self._finalize_choice(
                    chosen=picked,
                    candidates=candidates,
                    reason="cold_start",
                    considered=considered,
                    t0=t0,
                    variant=variant,
                    forced_pick=True,
                )
                await self._record_select(ctx, result, t0)
                return result

        # 6. Score everything.
        now = time.time()
        qw = self._config.quality_weight
        bw = self._config.bandit_weight
        quality_scores = [self._scorer.score(c, now=now) for c in candidates]
        bandit_samples = [self._bandit.sample(c, now=now) for c in candidates]
        final_scores = [
            _combine(q, b, qw, bw) for q, b in zip(quality_scores, bandit_samples)
        ]

        # 7. Policy -> probs -> fairness -> sample.
        probs = policy.probabilities(final_scores)
        probs = apply_fairness(probs, self._config.fairness)
        chosen_idx = _sample_index(probs, self._rng)
        chosen = candidates[chosen_idx]

        result = await self._finalize_choice(
            chosen=chosen,
            candidates=candidates,
            reason="selected",
            considered=considered,
            t0=t0,
            variant=variant,
            quality_scores=quality_scores,
            bandit_samples=bandit_samples,
            final_scores=final_scores,
            probabilities=probs,
        )
        await self._record_select(ctx, result, t0)
        return result

    async def feedback(
        self,
        order_id: str,
        entity_id: str,
        reward: Optional[float] = None,
        signal: str = "completed",
    ) -> bool:
        """Apply a reward signal.

        ``reward`` is optional — when ``None``, the value is derived from the
        ``signal`` per ``config.reward.partial_signals`` (accepted, completed)
        so callers can just say "accepted" / "completed" / "failed" without
        knowing the numeric mapping.

        Returns True if applied, False if it was a duplicate by ``order_id``.
        """
        t0 = time.perf_counter()
        applied_reward = self._resolve_reward(reward, signal)
        duplicate = False

        if order_id:
            claimed = await self._storage.claim_feedback_token(
                order_id, self._config.reward.timeout_sec
            )
            if not claimed:
                duplicate = True
                await self._record_feedback(
                    order_id, entity_id, applied_reward, signal, duplicate, t0
                )
                return False

        now = time.time()
        result = await self._storage.apply_feedback(
            entity_id,
            applied_reward,
            decay_factor=self._config.decay_factor,
            decay_interval_sec=self._config.decay_interval_sec,
            now=now,
        )
        if result is None:
            # Cold entity — initialise with prior + this update and save.
            stats = new_stats(
                entity_id,
                alpha=self._config.cold_start.bootstrap_alpha,
                beta=self._config.cold_start.bootstrap_beta,
            )
            self._bandit.update(stats, applied_reward, now=now)
            await self._storage.save(stats)

        if self._cb and self._cb.enabled:
            success = applied_reward >= 0.5
            await self._cb.record(entity_id, success=success)
            if not success:
                m.circuit_breaker_open_total.labels(name=self._config.name).inc()

        await self._record_feedback(
            order_id, entity_id, applied_reward, signal, duplicate, t0
        )
        return True

    async def upsert_metrics(
        self,
        entity_id: str,
        metrics: dict[str, MetricValue],
    ) -> None:
        stats = await self._storage.get(entity_id)
        if stats is None:
            stats = new_stats(
                entity_id,
                alpha=self._config.cold_start.bootstrap_alpha,
                beta=self._config.cold_start.bootstrap_beta,
            )
        stats.metrics.update(metrics)
        await self._storage.save(stats)

    async def enable(self, entity_id: str) -> None:
        await self._toggle(entity_id, enabled=True)

    async def disable(self, entity_id: str) -> None:
        await self._toggle(entity_id, enabled=False)

    async def _toggle(self, entity_id: str, *, enabled: bool) -> None:
        stats = await self._storage.get(entity_id)
        if stats is None:
            stats = new_stats(
                entity_id,
                alpha=self._config.cold_start.bootstrap_alpha,
                beta=self._config.cold_start.bootstrap_beta,
            )
        stats.enabled = enabled
        await self._storage.save(stats)

    def get_public_score(self, stats: EntityStats) -> int:
        q = self._scorer.score(stats)
        return int(round(q * 100))

    async def explain(self, entity_id: str) -> ExplanationReport:
        stats = await self._storage.get(entity_id)
        if stats is None:
            return ExplanationReport(
                entity_id=entity_id,
                stats=None,
                quality_score=None,
                metric_breakdown=[],
                bandit_mean=None,
                circuit_breaker=None,
            )
        q, breakdown = self._scorer.explain(stats)
        mean = stats.alpha / (stats.alpha + stats.beta)
        cb_state = (
            await self._cb.state(entity_id)
            if (self._cb and self._cb.enabled)
            else None
        )
        return ExplanationReport(
            entity_id=entity_id,
            stats=stats,
            quality_score=q,
            metric_breakdown=breakdown,
            bandit_mean=mean,
            circuit_breaker=cb_state,
        )

    # --- internals ----------------------------------------------------------

    def _resolve_reward(self, reward: Optional[float], signal: str) -> float:
        if reward is not None:
            return max(0.0, min(1.0, float(reward)))
        # Map known partial signals onto their configured numeric rewards.
        if signal == "accepted":
            return self._config.reward.accepted
        if signal == "completed":
            return self._config.reward.completed
        if signal == "failed" or signal == "timeout":
            return 0.0
        if signal == "success":
            return 1.0
        # Unknown signal → safe default (treat as completed).
        return self._config.reward.completed

    async def _finalize_choice(
        self,
        *,
        chosen: EntityStats,
        candidates: list[EntityStats],
        reason: str,
        considered: int,
        t0: float,
        variant: Optional[VariantAssignment] = None,
        forced_pick: bool = False,
        quality_scores: Optional[list[float]] = None,
        bandit_samples: Optional[list[float]] = None,
        final_scores: Optional[list[float]] = None,
        probabilities: Optional[list[float]] = None,
    ) -> SelectionResult:
        new_total = await self._storage.increment_selection_counter(chosen.entity_id)
        if new_total > 0:
            chosen.total_selections = new_total
        else:
            chosen.total_selections += 1
            await self._storage.save(chosen)

        cs: list[CandidateScore] = []
        if quality_scores and bandit_samples and final_scores and probabilities:
            for i, c in enumerate(candidates):
                cs.append(
                    CandidateScore(
                        entity_id=c.entity_id,
                        quality=quality_scores[i],
                        bandit_sample=bandit_samples[i],
                        final_score=final_scores[i],
                        probability=probabilities[i],
                    )
                )
            # Selection entropy gauge — informs "did the bandit collapse?"
            entropy = _shannon_entropy(probabilities)
            m.selection_entropy.labels(name=self._config.name).set(entropy)
        elif forced_pick:
            uniform = 1.0 / len(candidates)
            for c in candidates:
                cs.append(
                    CandidateScore(
                        entity_id=c.entity_id,
                        quality=0.0,
                        bandit_sample=0.0,
                        final_score=0.0,
                        probability=uniform,
                    )
                )

        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return SelectionResult(
            entity_id=chosen.entity_id,
            reason=reason,
            candidates_considered=considered,
            candidates_after_filters=len(candidates),
            latency_ms=elapsed_ms,
            candidates=cs,
            experiment_variant=variant.variant if variant else None,
        )

    async def _record_select(
        self,
        ctx: SelectionContext,
        result: SelectionResult,
        t0: float,
    ) -> None:
        latency_s = (time.perf_counter() - t0)
        m.select_latency_seconds.labels(name=self._config.name).observe(latency_s)
        m.select_total.labels(name=self._config.name, result=result.reason).inc()
        try:
            await self._sink.emit_decision(
                DecisionEvent(
                    ts=time.time(),
                    selector_name=self._config.name,
                    order_id=ctx.order_id,
                    chosen_entity_id=result.entity_id,
                    candidates=[
                        (c.entity_id, c.quality, c.bandit_sample, c.final_score, c.probability)
                        for c in result.candidates
                    ],
                    reason=result.reason,
                    context={
                        "amount": ctx.amount,
                        "currency": ctx.currency,
                        "user_segment": ctx.user_segment,
                        "require_tags": ctx.require_tags,
                        **(ctx.extra or {}),
                    },
                    experiment_variant=result.experiment_variant,
                    decision_latency_ms=result.latency_ms,
                )
            )
        except Exception as exc:  # noqa: BLE001 — sink failures must not affect routing
            logger.warning("selector_sink_failed: %s", exc)

    async def _record_feedback(
        self,
        order_id: str,
        entity_id: str,
        reward: float,
        signal: str,
        duplicate: bool,
        t0: float,
    ) -> None:
        latency_s = (time.perf_counter() - t0)
        m.feedback_latency_seconds.labels(name=self._config.name).observe(latency_s)
        m.feedback_total.labels(
            name=self._config.name,
            signal=signal,
            reward_bucket=m.reward_bucket(reward),
        ).inc()
        try:
            await self._sink.emit_feedback(
                FeedbackEvent(
                    ts=time.time(),
                    selector_name=self._config.name,
                    order_id=order_id,
                    entity_id=entity_id,
                    reward=reward,
                    signal=signal,
                    duplicate=duplicate,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("selector_sink_failed: %s", exc)


def _combine(quality: float, bandit: float, qw: float, bw: float) -> float:
    q = max(quality, 1e-9)
    b = max(bandit, 1e-9)
    return (q ** qw) * (b ** bw)


def _sample_index(probs: list[float], rng: random.Random) -> int:
    r = rng.random()
    cumulative = 0.0
    last = 0
    for i, p in enumerate(probs):
        cumulative += p
        last = i
        if r < cumulative:
            return i
    return last


def _shannon_entropy(probs: list[float]) -> float:
    """Returns 0 for collapsed distributions, log(N) for uniform."""
    total = 0.0
    for p in probs:
        if p > 0:
            total -= p * math.log(p)
    return total


def _empty_result(reason: str, considered: int, t0: float) -> SelectionResult:
    return SelectionResult(
        entity_id=None,
        reason=reason,
        candidates_considered=considered,
        candidates_after_filters=0,
        latency_ms=(time.perf_counter() - t0) * 1000.0,
    )


def hash_to_bucket(order_id: str, buckets: int) -> int:
    h = hashlib.sha256(order_id.encode("utf-8")).hexdigest()
    return int(h[:8], 16) % buckets
