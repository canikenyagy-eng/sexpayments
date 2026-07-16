"""Admin HTTP API for the Multi-Armed Bandit selector subsystem.

Endpoints (all under ``/api/v1/selectors``):

  GET    /                                — list configured selectors
  GET    /{name}/stats                    — selector-level state
  GET    /{name}/entities/{eid}/explain   — per-entity breakdown
  POST   /{name}/select                   — dry-run a selection (admin tooling)
  POST   /{name}/feedback                 — push a reward signal
  PUT    /{name}/entities/{eid}/metrics   — upsert metrics for an entity
  PATCH  /{name}/entities/{eid}/state     — enable/disable an entity
  POST   /reload                          — trigger registry reload

Every endpoint is admin-gated by the router's dependency list, so we do
NOT repeat ``Depends(require_admin)`` on individual handlers (per
CLAUDE.md anti-pattern list).
"""
from __future__ import annotations

import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.exceptions import NotFoundException
from app.modules.selector import bootstrap as selector_bootstrap
from app.modules.selector.api.schemas import (
    AdminCandidateScore,
    AdminEntityStateUpdate,
    AdminExplanationResponse,
    AdminFeedbackRequest,
    AdminMetricsUpsertRequest,
    AdminScoreBreakdown,
    AdminSelectRequest,
    AdminSelectionResponse,
    AdminSelectorStats,
)
from app.modules.selector.core.selector import SelectionContext
from app.modules.selector.core.stats import MetricValue
from app.modules.selector.registry import SelectorRegistry
from app.modules.users.permissions import require_admin


router = APIRouter(dependencies=[Depends(require_admin)])


def _registry() -> SelectorRegistry:
    reg = selector_bootstrap.get_registry()
    if reg is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="selector registry is not initialised",
        )
    return reg


@router.get("", response_model=List[str], summary="List configured selectors")
async def list_selectors():
    return _registry().names()


@router.get(
    "/{name}/stats",
    response_model=AdminSelectorStats,
    summary="Selector-level stats",
)
async def selector_stats(name: str):
    reg = _registry()
    try:
        sel = reg.get(name)
    except KeyError:
        raise NotFoundException(f"selector {name!r} not found")
    ids = await sel._storage.list_entity_ids()
    enabled = 0
    if ids:
        # Cheap: only count enabled by sampling the keys we already have.
        got = await sel._storage.get_many(ids)
        enabled = sum(1 for s in got.values() if s.enabled)
    return AdminSelectorStats(
        name=sel.name,
        namespace=sel.config.namespace,
        enabled=sel.config.enabled,
        bandit_strategy=sel.config.bandit_strategy,
        policy=sel.config.policy,
        config_version=reg.version,
        entity_count=len(ids),
        enabled_entity_count=enabled,
    )


@router.get(
    "/{name}/entities/{entity_id}/explain",
    response_model=AdminExplanationResponse,
    summary="Per-entity score + bandit state breakdown",
)
async def explain_entity(name: str, entity_id: str):
    try:
        sel = _registry().get(name)
    except KeyError:
        raise NotFoundException(f"selector {name!r} not found")
    report = await sel.explain(entity_id)
    return AdminExplanationResponse(
        entity_id=entity_id,
        enabled=(report.stats.enabled if report.stats else None),
        alpha=(report.stats.alpha if report.stats else None),
        beta=(report.stats.beta if report.stats else None),
        total_selections=(
            report.stats.total_selections if report.stats else None
        ),
        quality_score=report.quality_score,
        bandit_mean=report.bandit_mean,
        public_score=(
            sel.get_public_score(report.stats) if report.stats else None
        ),
        metric_breakdown=[
            AdminScoreBreakdown(
                name=b.name,
                raw_value=b.raw_value,
                normalized=b.normalized,
                weight=b.weight,
                contribution=b.contribution,
                skipped_reason=b.skipped_reason,
            )
            for b in report.metric_breakdown
        ],
        circuit_breaker=report.circuit_breaker,
    )


@router.post(
    "/{name}/select",
    response_model=AdminSelectionResponse,
    summary="Dry-run a selection (for admin tooling)",
)
async def admin_select(name: str, payload: AdminSelectRequest):
    try:
        sel = _registry().get(name)
    except KeyError:
        raise NotFoundException(f"selector {name!r} not found")
    ctx = SelectionContext(
        order_id=payload.order_id,
        amount=payload.amount,
        currency=payload.currency,
        user_segment=payload.user_segment,
        require_tags=payload.require_tags,
        extra=payload.extra,
    )
    result = await sel.select(payload.candidate_ids, ctx)
    return AdminSelectionResponse(
        entity_id=result.entity_id,
        reason=result.reason,
        candidates_considered=result.candidates_considered,
        candidates_after_filters=result.candidates_after_filters,
        latency_ms=result.latency_ms,
        candidates=[
            AdminCandidateScore(
                entity_id=c.entity_id,
                quality=c.quality,
                bandit_sample=c.bandit_sample,
                final_score=c.final_score,
                probability=c.probability,
            )
            for c in result.candidates
        ],
        experiment_variant=result.experiment_variant,
    )


@router.post(
    "/{name}/feedback",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Push a reward signal",
)
async def admin_feedback(name: str, payload: AdminFeedbackRequest):
    try:
        sel = _registry().get(name)
    except KeyError:
        raise NotFoundException(f"selector {name!r} not found")
    await sel.feedback(
        order_id=payload.order_id,
        entity_id=payload.entity_id,
        reward=payload.reward,
        signal=payload.signal,
    )


@router.put(
    "/{name}/entities/{entity_id}/metrics",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Upsert business metrics for an entity",
)
async def upsert_entity_metrics(
    name: str,
    entity_id: str,
    payload: AdminMetricsUpsertRequest,
):
    try:
        sel = _registry().get(name)
    except KeyError:
        raise NotFoundException(f"selector {name!r} not found")
    metrics = {
        m.name: MetricValue(
            value=m.value,
            sample_size=m.sample_size,
            updated_at=m.updated_at if m.updated_at is not None else time.time(),
        )
        for m in payload.metrics
    }
    await sel.upsert_metrics(entity_id, metrics)


@router.patch(
    "/{name}/entities/{entity_id}/state",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Enable or disable an entity",
)
async def patch_entity_state(
    name: str,
    entity_id: str,
    payload: AdminEntityStateUpdate,
):
    try:
        sel = _registry().get(name)
    except KeyError:
        raise NotFoundException(f"selector {name!r} not found")
    if payload.enabled:
        await sel.enable(entity_id)
    else:
        await sel.disable(entity_id)


@router.post(
    "/reload",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Reload selector config from disk",
)
async def reload_registry():
    """Reload from the path the bootstrap module remembers.

    If no path is wired (e.g. config was constructed in-memory), this is a
    no-op and returns 204 — preferable to a 500 because hot-reload is a
    best-effort capability.
    """
    reg = _registry()
    path = getattr(reg, "_config_path", None)
    if not path:
        return
    from app.modules.selector.config.loader import load_from_yaml

    new_cfg = load_from_yaml(path)
    reg.reload(new_cfg)
