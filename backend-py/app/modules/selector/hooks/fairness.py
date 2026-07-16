"""Fairness constraints applied to the final probability vector.

- min_share: every active entity must get at least ``min_share / N`` mass,
  so the bandit can't permanently starve an arm of exploration traffic.
- max_share: cap any single entity at ``max_share`` to avoid funnelling all
  traffic to one trader / provider even if the bandit is convinced.

We apply max first (clip the leaders, redistribute slack to non-leaders),
then min (floor everyone else, renormalize). Iterating once is "good enough"
in practice — exact constraint satisfaction requires more rounds and isn't
needed at our scale.
"""
from __future__ import annotations

from typing import Sequence

from app.modules.selector.config.models import FairnessConfig


def apply_fairness(probs: Sequence[float], config: FairnessConfig) -> list[float]:
    n = len(probs)
    if n == 0:
        return []
    out = list(probs)
    if config.max_share < 1.0:
        out = _cap_max(out, config.max_share)
    if config.min_share > 0.0:
        floor = config.min_share / n
        out = _floor_min(out, floor)
    return out


def _cap_max(probs: list[float], cap: float) -> list[float]:
    """Cap each prob at ``cap``; redistribute the overflow to the under-cap mass."""
    excess = 0.0
    capped = []
    under_cap_total = 0.0
    for p in probs:
        if p > cap:
            excess += p - cap
            capped.append(cap)
        else:
            capped.append(p)
            under_cap_total += p
    if excess <= 0 or under_cap_total <= 0:
        return capped
    # Spread excess proportionally to under-cap mass, then re-clip if needed.
    result = []
    for p in capped:
        if p < cap:
            share = (p / under_cap_total) * excess
            new_p = p + share
            result.append(min(new_p, cap))
        else:
            result.append(p)
    total = sum(result)
    if total <= 0:
        return [1.0 / len(probs)] * len(probs)
    return [p / total for p in result]


def _floor_min(probs: list[float], floor: float) -> list[float]:
    """Lift each prob to ``floor``, then renormalize."""
    out = [max(p, floor) for p in probs]
    total = sum(out)
    if total <= 0:
        return [1.0 / len(probs)] * len(probs)
    return [p / total for p in out]
