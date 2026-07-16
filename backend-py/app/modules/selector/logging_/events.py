"""Event records logged on each select/feedback.

These map 1:1 onto the ClickHouse tables in ``app/infrastructure/clickhouse/schema/selector.sql`` and the
Kafka topics ``selector.decisions`` / ``selector.feedback`` so a sink can ship
them anywhere without translation.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class DecisionEvent:
    ts: float
    selector_name: str
    order_id: str
    chosen_entity_id: Optional[str]
    candidates: list[tuple[str, float, float, float, float]]
    reason: str
    context: dict[str, Any]
    experiment_variant: Optional[str] = None
    decision_latency_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "ts": self.ts,
            "selector_name": self.selector_name,
            "order_id": self.order_id,
            "chosen_entity_id": self.chosen_entity_id,
            "candidates": [
                {
                    "entity_id": eid,
                    "quality": q,
                    "bandit_sample": b,
                    "final_score": f,
                    "probability": p,
                }
                for (eid, q, b, f, p) in self.candidates
            ],
            "reason": self.reason,
            "context": self.context,
            "experiment_variant": self.experiment_variant or "",
            "decision_latency_ms": self.decision_latency_ms,
        }


@dataclass
class FeedbackEvent:
    ts: float
    selector_name: str
    order_id: str
    entity_id: str
    reward: float
    signal: str
    duplicate: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def now_ts() -> float:
    return time.time()
