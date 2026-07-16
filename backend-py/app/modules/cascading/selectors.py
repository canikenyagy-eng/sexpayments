"""Algorithms for choosing among cascade providers.

Two strategies:
  * GROUPED — race within a tier, escalate to the next tier on timeout / refusal.
  * POOLED — sequential walk over all active providers ranked by score.

Score is computed from the last hour of CascadeOrderAttempt aggregates: higher
is better. Formula: ``priority_weight * (success_rate or epsilon) /
max(avg_latency_ms / 1000, 0.1)``. The epsilon keeps brand-new providers
discoverable instead of permanently zeroed out by zero history.
"""
from __future__ import annotations

from datetime import timedelta
from typing import List, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from app.common.types import utcnow
from app.modules.cascading.models import CascadeProvider
from app.modules.cascading.repository import (
    CascadeOrderAttemptRepository,
    CascadeProviderRepository,
)


_EPSILON_SUCCESS_RATE = 0.05  # untracked providers still get a chance


async def rank_pool_providers(
    session: AsyncSession,
    *,
    window_seconds: int = 3600,
) -> List[CascadeProvider]:
    """Rank all active providers by recent score for POOLED mode."""
    providers = await CascadeProviderRepository(session).list_active()
    if not providers:
        return []

    attempt_repo = CascadeOrderAttemptRepository(session)
    since = utcnow() - timedelta(seconds=window_seconds)

    scored: List[Tuple[float, CascadeProvider]] = []
    for provider in providers:
        agg = await attempt_repo.aggregate_for_provider(provider.id, since)
        success_rate = agg["success_rate"] or _EPSILON_SUCCESS_RATE
        avg_latency_s = max((agg["avg_latency_ms"] or 0) / 1000.0, 0.1)
        score = provider.priority_weight * success_rate / avg_latency_s
        scored.append((score, provider))

    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return [provider for _, provider in scored]
