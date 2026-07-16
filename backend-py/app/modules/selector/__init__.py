"""Multi-Armed Bandit selector subsystem.

Replaces random/round-robin entity selection (traders, providers, ...) with
a configurable engine combining a static quality score (from business
metrics) and a dynamic bandit score (Thompson sampling by default).

Public entry point: ``SelectorRegistry``. See registry.py for usage.
"""
from app.modules.selector.config import (
    MetricSpec,
    RootConfig,
    SelectorConfig,
    load_from_dict,
    load_from_yaml,
)
from app.modules.selector.core import (
    EntitySelector,
    EntityStats,
    ExplanationReport,
    MetricValue,
    SelectionContext,
    SelectionResult,
    new_stats,
)
from app.modules.selector.factory import build_memory_selector, build_selector
from app.modules.selector.registry import SelectorRegistry
from app.modules.selector.storage import MemoryStorage, RedisStorage

__all__ = [
    "EntitySelector",
    "EntityStats",
    "ExplanationReport",
    "MemoryStorage",
    "MetricSpec",
    "MetricValue",
    "RedisStorage",
    "RootConfig",
    "SelectionContext",
    "SelectionResult",
    "SelectorConfig",
    "SelectorRegistry",
    "build_memory_selector",
    "build_selector",
    "load_from_dict",
    "load_from_yaml",
    "new_stats",
]
