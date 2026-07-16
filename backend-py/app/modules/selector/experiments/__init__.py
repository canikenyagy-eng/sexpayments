"""A/B routing and offline policy evaluation helpers.

``OfflineSimulator`` and ``HistoricalEvent`` live in
``experiments.simulator`` and must be imported from there explicitly —
they depend on the top-level selector package, so re-exporting them here
would create a circular import. Same story for ``SimulationReport``.
"""
from app.modules.selector.experiments.ab import (
    ABRouter,
    RedisStickyStore,
    StickyStore,
    VariantAssignment,
    assign_variant,
)
from app.modules.selector.experiments.counterfactual import (
    LoggedEvent,
    clip_weights,
    direct_method,
    doubly_robust,
    ips,
    snips,
)

__all__ = [
    "ABRouter",
    "LoggedEvent",
    "RedisStickyStore",
    "StickyStore",
    "VariantAssignment",
    "assign_variant",
    "clip_weights",
    "direct_method",
    "doubly_robust",
    "ips",
    "snips",
]
