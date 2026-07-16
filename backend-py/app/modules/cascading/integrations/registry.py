"""Registry of provider adapters.

Adapters register themselves automatically: every ``ProviderAdapter`` subclass
defined in any ``app.modules.cascading.integrations.*`` module is picked up at
import time. Adding a new provider is therefore **literally** "drop one file
in this directory" — no edits anywhere else.

Manual ``register()`` is still exposed for tests that need to inject a stub
adapter at runtime (see test_cascading_service.py's _ScriptedAdapter pattern).
"""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Dict, List

from app.modules.cascading.integrations.base import ProviderAdapter


logger = logging.getLogger(__name__)


_REGISTRY: Dict[str, ProviderAdapter] = {}
_AUTODISCOVERED = False


def register(adapter: ProviderAdapter) -> None:
    """Register an adapter instance by its ``code``. Used by tests and the
    auto-discovery routine below.
    """
    if not adapter.code:
        raise ValueError("ProviderAdapter must define a non-empty `code`.")
    _REGISTRY[adapter.code] = adapter


def get(code: str) -> ProviderAdapter:
    _ensure_autodiscovered()
    try:
        return _REGISTRY[code]
    except KeyError:
        raise KeyError(f"No cascade adapter registered for code={code!r}")


def has(code: str) -> bool:
    _ensure_autodiscovered()
    return code in _REGISTRY


def list_codes() -> List[str]:
    _ensure_autodiscovered()
    return sorted(_REGISTRY.keys())


def _ensure_autodiscovered() -> None:
    """Lazy one-shot scan of the integrations package."""
    global _AUTODISCOVERED
    if _AUTODISCOVERED:
        return
    _AUTODISCOVERED = True

    package = importlib.import_module(__package__)
    skip = {"__init__", "base", "registry"}

    for module_info in pkgutil.iter_modules(package.__path__):
        if module_info.name in skip or module_info.ispkg:
            continue
        full_name = f"{__package__}.{module_info.name}"
        try:
            module = importlib.import_module(full_name)
        except Exception as exc:
            logger.warning("cascade_adapter_import_failed module=%s error=%s", full_name, exc)
            continue
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, ProviderAdapter)
                and obj is not ProviderAdapter
                and obj.__module__ == module.__name__
                and obj.code
                and obj.code not in _REGISTRY
            ):
                try:
                    register(obj())
                except Exception as exc:
                    logger.warning(
                        "cascade_adapter_register_failed adapter=%s error=%s",
                        obj.__name__,
                        exc,
                    )


# Eagerly run discovery on first import so the public API is ready immediately.
_ensure_autodiscovered()
