"""File-mtime polling watcher for hot config reload.

A heavyweight inotify/watchdog dependency is overkill here — config files
are touched maybe a few times a day. We just stat() the file periodically
and reload when mtime changes. The watcher runs as an asyncio task; cancel
it during graceful shutdown via ``stop()``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from app.modules.selector.config.loader import load_from_yaml
from app.modules.selector.registry import SelectorRegistry


logger = logging.getLogger(__name__)


class ConfigReloadWatcher:
    def __init__(
        self,
        registry: SelectorRegistry,
        path: str,
        *,
        poll_sec: float = 5.0,
    ):
        if not path:
            raise ValueError("path must be a non-empty file path")
        self._registry = registry
        self._path = path
        self._poll = poll_sec
        self._mtime: Optional[float] = None
        self._task: Optional[asyncio.Task] = None
        self._stopped = asyncio.Event()
        # Remember the path on the registry so the /reload endpoint can find it.
        setattr(self._registry, "_config_path", path)

    def start(self) -> None:
        if self._task is not None:
            return
        try:
            self._mtime = os.path.getmtime(self._path)
        except OSError:
            self._mtime = None
        self._task = asyncio.create_task(self._run())
        logger.info(
            "selector_config_watcher_started",
            extra={"path": self._path, "poll_sec": self._poll},
        )

    async def stop(self) -> None:
        self._stopped.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None

    async def _run(self) -> None:
        while not self._stopped.is_set():
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self._poll)
                return
            except asyncio.TimeoutError:
                pass
            await self._maybe_reload()

    async def _maybe_reload(self) -> None:
        try:
            mtime = os.path.getmtime(self._path)
        except OSError as exc:
            logger.warning(
                "selector_config_stat_failed",
                extra={"path": self._path, "error": str(exc)},
            )
            return
        if self._mtime is not None and mtime <= self._mtime:
            return
        try:
            new_cfg = load_from_yaml(self._path)
        except Exception as exc:  # noqa: BLE001 — broken yaml shouldn't crash the app
            logger.warning(
                "selector_config_reload_failed",
                extra={"path": self._path, "error": str(exc)},
            )
            return
        try:
            self._registry.reload(new_cfg)
            self._mtime = mtime
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "selector_config_reload_apply_failed",
                extra={"error": str(exc)},
            )
