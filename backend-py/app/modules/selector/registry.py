"""Registry — single facade the rest of the app talks to.

Usage:
    registry = SelectorRegistry(root_config, redis_client=client)
    selector = registry.get("traders")
    result = await selector.select(candidate_ids, context)
"""
from __future__ import annotations

import logging
from typing import Optional

import redis.asyncio as redis

from app.modules.selector import metrics as m
from app.modules.selector.config.models import RootConfig, SelectorConfig
from app.modules.selector.core.selector import EntitySelector
from app.modules.selector.factory import build_selector
from app.modules.selector.logging_.sinks import EventSink
from app.modules.selector.storage.protocol import SelectorStorage


logger = logging.getLogger(__name__)


class SelectorRegistry:
    def __init__(
        self,
        config: RootConfig,
        *,
        redis_client: Optional[redis.Redis] = None,
        event_sink: Optional[EventSink] = None,
        storage_overrides: Optional[dict[str, SelectorStorage]] = None,
    ):
        self._config = config
        self._redis = redis_client
        self._sink = event_sink
        self._overrides = dict(storage_overrides or {})
        self._instances: dict[str, EntitySelector] = {}
        self._version = 0
        self._build_all()

    def _build_all(self) -> None:
        for name, cfg in self._config.selectors.items():
            self._instances[name] = self._build_one(cfg)
        self._bump_version()

    def _build_one(self, cfg: SelectorConfig) -> EntitySelector:
        storage = self._overrides.get(cfg.name)
        return build_selector(
            cfg,
            storage=storage,
            redis_client=self._redis,
            event_sink=self._sink,
        )

    def _bump_version(self) -> None:
        self._version += 1
        for name in self._instances:
            m.config_version.labels(name=name).set(self._version)

    def get(self, name: str) -> EntitySelector:
        try:
            return self._instances[name]
        except KeyError as exc:
            raise KeyError(f"selector {name!r} is not registered") from exc

    def names(self) -> list[str]:
        return list(self._instances.keys())

    def reload(self, new_config: RootConfig) -> None:
        """Hot-swap config. Redis state survives because it's keyed by namespace."""
        old_names = set(self._instances.keys())
        new_names = set(new_config.selectors.keys())

        for removed in old_names - new_names:
            logger.info("selector_removed_on_reload", extra={"selector": removed})
            self._instances.pop(removed, None)

        for name in new_names:
            cfg = new_config.selectors[name]
            self._instances[name] = self._build_one(cfg)

        self._config = new_config
        self._bump_version()
        logger.info(
            "selector_registry_reloaded",
            extra={"selectors": list(new_names), "version": self._version},
        )

    @property
    def config(self) -> RootConfig:
        return self._config

    @property
    def version(self) -> int:
        return self._version
