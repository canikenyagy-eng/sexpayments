"""Mutable state per entity (trader / provider / whatever the selector is over).

This is the canonical in-process representation. The Storage layer is in
charge of (de)serialising it to/from Redis or whatever backend is bound.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = 2


# Lazy migrators run on read whenever the stored schema_version is older
# than SCHEMA_VERSION. Each is keyed by the version it upgrades FROM and
# returns a dict with schema_version bumped to from+1.
#
# Example: v1 → v2 introduced the ``tags`` field; older payloads simply
# didn't have it. _migrate_v1_to_v2 just inserts the default {}.

def _migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    data.setdefault("tags", {})
    data["schema_version"] = 2
    return data


_MIGRATORS: dict[int, "callable"] = {
    1: _migrate_v1_to_v2,
}


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Walk a stored payload up to the current SCHEMA_VERSION."""
    current = int(data.get("schema_version", 1))
    while current < SCHEMA_VERSION:
        migrator = _MIGRATORS.get(current)
        if migrator is None:
            # No migrator registered for this version — bump and hope.
            data["schema_version"] = current + 1
        else:
            data = migrator(data)
        current = int(data.get("schema_version", current + 1))
    return data


@dataclass
class MetricValue:
    value: float
    sample_size: int = 0
    updated_at: float = field(default_factory=time.time)


@dataclass
class EntityStats:
    entity_id: str
    metrics: dict[str, MetricValue] = field(default_factory=dict)
    alpha: float = 1.0
    beta: float = 1.0
    last_updated: float = field(default_factory=time.time)
    total_selections: int = 0
    enabled: bool = True
    tags: dict[str, str] = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "metrics": {
                name: asdict(mv) for name, mv in self.metrics.items()
            },
            "alpha": self.alpha,
            "beta": self.beta,
            "last_updated": self.last_updated,
            "total_selections": self.total_selections,
            "enabled": self.enabled,
            "tags": dict(self.tags),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EntityStats":
        if int(data.get("schema_version", 1)) < SCHEMA_VERSION:
            data = _migrate(dict(data))
        metrics_raw = data.get("metrics", {}) or {}
        metrics = {
            name: MetricValue(
                value=float(mv["value"]),
                sample_size=int(mv.get("sample_size", 0)),
                updated_at=float(mv.get("updated_at", 0.0)),
            )
            for name, mv in metrics_raw.items()
        }
        return cls(
            entity_id=str(data["entity_id"]),
            metrics=metrics,
            alpha=float(data.get("alpha", 1.0)),
            beta=float(data.get("beta", 1.0)),
            last_updated=float(data.get("last_updated", time.time())),
            total_selections=int(data.get("total_selections", 0)),
            enabled=bool(data.get("enabled", True)),
            tags=dict(data.get("tags") or {}),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        )


def new_stats(
    entity_id: str,
    *,
    alpha: float = 1.0,
    beta: float = 1.0,
    enabled: bool = True,
    tags: dict[str, str] | None = None,
) -> EntityStats:
    return EntityStats(
        entity_id=entity_id,
        alpha=alpha,
        beta=beta,
        last_updated=time.time(),
        enabled=enabled,
        tags=dict(tags or {}),
    )
