"""Admin-facing pydantic schemas for the selector HTTP API.

Per project CLAUDE.md: schemas are role-prefixed (``Admin*``) and live in
their own file so other roles can't accidentally take a dependency on
admin-shaped responses.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class AdminMetricInput(BaseModel):
    name: str
    value: float
    sample_size: int = 0
    updated_at: Optional[float] = None


class AdminMetricsUpsertRequest(BaseModel):
    metrics: list[AdminMetricInput]


class AdminSelectRequest(BaseModel):
    candidate_ids: list[str]
    order_id: str = ""
    amount: Optional[float] = None
    currency: Optional[str] = None
    user_segment: Optional[str] = None
    require_tags: Optional[dict[str, str]] = None
    extra: dict = Field(default_factory=dict)


class AdminCandidateScore(BaseModel):
    entity_id: str
    quality: float
    bandit_sample: float
    final_score: float
    probability: float


class AdminSelectionResponse(BaseModel):
    entity_id: Optional[str]
    reason: str
    candidates_considered: int
    candidates_after_filters: int
    latency_ms: float
    candidates: list[AdminCandidateScore]
    experiment_variant: Optional[str] = None


class AdminFeedbackRequest(BaseModel):
    order_id: str
    entity_id: str
    reward: Optional[float] = None
    signal: str = "completed"


class AdminEntityStateUpdate(BaseModel):
    enabled: bool


class AdminScoreBreakdown(BaseModel):
    name: str
    raw_value: Optional[float]
    normalized: float
    weight: float
    contribution: float
    skipped_reason: Optional[str] = None


class AdminExplanationResponse(BaseModel):
    entity_id: str
    enabled: Optional[bool]
    alpha: Optional[float]
    beta: Optional[float]
    total_selections: Optional[int]
    quality_score: Optional[float]
    bandit_mean: Optional[float]
    public_score: Optional[int]
    metric_breakdown: list[AdminScoreBreakdown]
    circuit_breaker: Optional[dict] = None


class AdminSelectorStats(BaseModel):
    name: str
    namespace: str
    enabled: bool
    bandit_strategy: str
    policy: str
    config_version: int
    entity_count: int
    enabled_entity_count: int
