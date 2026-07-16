"""Counterfactual policy value estimators.

When you simulate a new policy against logged data, most events involve a
candidate that *production* didn't pick — and you have no direct reward
signal for the counterfactual choice. Two standard estimators close that
gap:

  * Inverse Propensity Scoring (IPS) — re-weights observed rewards by
    p_new(a|x) / p_logging(a|x). Unbiased if propensities are accurate,
    but high-variance when the new policy diverges sharply.

  * Direct Method (DM) — fits a reward model r̂(a, x) and averages it.
    Low-variance, biased if the model is wrong.

  * Doubly Robust (DR) — combines them. Unbiased if either piece is right.

Inputs are simple per-event records so callers can plug in any reward
model they want. We don't ship a reward model with the module — that's
domain-specific (uplift on conversion, dispute risk, …).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable, Optional


@dataclass
class LoggedEvent:
    order_id: str
    chosen_entity_id: str
    candidates: list[str]
    reward: float
    propensity: float
    new_action_prob: float
    """Probability the new policy would have chosen the same entity for this event."""
    reward_estimate_old: Optional[float] = None
    """r̂ under the logging action — needed for DR only."""
    reward_estimate_new: Optional[float] = None
    """r̂ under the new policy's action — needed for DM and DR."""


def ips(events: Iterable[LoggedEvent]) -> float:
    """Standard IPS — average of (p_new / p_log) * observed reward."""
    total = 0.0
    n = 0
    for ev in events:
        if ev.propensity <= 0:
            continue
        weight = ev.new_action_prob / ev.propensity
        total += weight * ev.reward
        n += 1
    return total / n if n else 0.0


def snips(events: Iterable[LoggedEvent]) -> float:
    """Self-normalized IPS — divides by sum of weights instead of count. Less
    variance, biased when weights are extreme, but a safer default for small
    samples (and the one we recommend for selector A/B reads)."""
    weighted_reward = 0.0
    weight_sum = 0.0
    for ev in events:
        if ev.propensity <= 0:
            continue
        w = ev.new_action_prob / ev.propensity
        weighted_reward += w * ev.reward
        weight_sum += w
    return weighted_reward / weight_sum if weight_sum > 0 else 0.0


def direct_method(events: Iterable[LoggedEvent]) -> float:
    """Average r̂ under the new policy. Requires reward_estimate_new on every event."""
    total = 0.0
    n = 0
    for ev in events:
        if ev.reward_estimate_new is None:
            continue
        total += ev.reward_estimate_new
        n += 1
    return total / n if n else 0.0


def doubly_robust(events: Iterable[LoggedEvent]) -> float:
    """DR — DM + IPS-corrected residual. Requires both r̂ estimates and propensities."""
    total = 0.0
    n = 0
    for ev in events:
        if ev.propensity <= 0:
            continue
        if (
            ev.reward_estimate_old is None
            or ev.reward_estimate_new is None
        ):
            continue
        weight = ev.new_action_prob / ev.propensity
        residual = weight * (ev.reward - ev.reward_estimate_old)
        total += ev.reward_estimate_new + residual
        n += 1
    return total / n if n else 0.0


def clip_weights(
    events: Iterable[LoggedEvent], *, max_weight: float = 10.0
) -> list[LoggedEvent]:
    """Helper: clip extreme propensity ratios to bound IPS variance.

    Returns a fresh list; the originals are not mutated.
    """
    out: list[LoggedEvent] = []
    for ev in events:
        if ev.propensity <= 0:
            out.append(ev)
            continue
        w = ev.new_action_prob / ev.propensity
        if w > max_weight:
            scale = max_weight / w
            out.append(
                LoggedEvent(
                    order_id=ev.order_id,
                    chosen_entity_id=ev.chosen_entity_id,
                    candidates=ev.candidates,
                    reward=ev.reward,
                    propensity=ev.propensity,
                    new_action_prob=ev.new_action_prob * scale,
                    reward_estimate_old=ev.reward_estimate_old,
                    reward_estimate_new=ev.reward_estimate_new,
                )
            )
        else:
            out.append(ev)
    return out
