"""File-mtime polling watcher."""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

from app.modules.selector import MemoryStorage, SelectorRegistry, load_from_dict
from app.modules.selector.config.reload import ConfigReloadWatcher


def _yaml_text(namespace: str) -> str:
    return f"""
selectors:
  traders:
    namespace: "{namespace}"
"""


@pytest.mark.asyncio
async def test_watcher_reloads_on_mtime_bump(tmp_path):
    path = tmp_path / "selectors.yaml"
    path.write_text(_yaml_text("ns:v1"))

    cfg = load_from_dict({"selectors": {"traders": {"namespace": "ns:v1"}}})
    reg = SelectorRegistry(cfg, storage_overrides={"traders": MemoryStorage()})

    watcher = ConfigReloadWatcher(reg, str(path), poll_sec=0.05)
    watcher.start()
    try:
        # Wait one poll cycle for the initial mtime capture.
        await asyncio.sleep(0.1)
        # Touch the file in the future to ensure mtime > captured.
        future_mtime = path.stat().st_mtime + 5
        path.write_text(_yaml_text("ns:v2"))
        os.utime(str(path), (future_mtime, future_mtime))
        # Wait for the watcher to notice.
        for _ in range(40):
            await asyncio.sleep(0.05)
            if reg.get("traders").config.namespace == "ns:v2":
                break
        assert reg.get("traders").config.namespace == "ns:v2"
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_survives_broken_yaml(tmp_path):
    """Bad yaml is ignored — previous config stays active."""
    path = tmp_path / "selectors.yaml"
    path.write_text(_yaml_text("ns:v1"))
    cfg = load_from_dict({"selectors": {"traders": {"namespace": "ns:v1"}}})
    reg = SelectorRegistry(cfg, storage_overrides={"traders": MemoryStorage()})
    watcher = ConfigReloadWatcher(reg, str(path), poll_sec=0.05)
    watcher.start()
    try:
        await asyncio.sleep(0.1)
        # Corrupt the file.
        path.write_text("not: [valid:: yaml :")
        future_mtime = path.stat().st_mtime + 5
        os.utime(str(path), (future_mtime, future_mtime))
        await asyncio.sleep(0.2)
        # Config unchanged.
        assert reg.get("traders").config.namespace == "ns:v1"
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_handles_missing_file(tmp_path):
    """Deleted file → watcher logs and continues."""
    path = tmp_path / "absent.yaml"
    path.write_text(_yaml_text("ns:v1"))
    cfg = load_from_dict({"selectors": {"traders": {"namespace": "ns:v1"}}})
    reg = SelectorRegistry(cfg, storage_overrides={"traders": MemoryStorage()})
    watcher = ConfigReloadWatcher(reg, str(path), poll_sec=0.05)
    watcher.start()
    try:
        await asyncio.sleep(0.1)
        path.unlink()
        await asyncio.sleep(0.2)
        # Doesn't crash, config preserved.
        assert reg.get("traders").config.namespace == "ns:v1"
    finally:
        await watcher.stop()


@pytest.mark.asyncio
async def test_watcher_stores_config_path_on_registry(tmp_path):
    path = tmp_path / "selectors.yaml"
    path.write_text(_yaml_text("ns:v1"))
    cfg = load_from_dict({"selectors": {"traders": {"namespace": "ns:v1"}}})
    reg = SelectorRegistry(cfg, storage_overrides={"traders": MemoryStorage()})
    watcher = ConfigReloadWatcher(reg, str(path))
    try:
        assert getattr(reg, "_config_path", None) == str(path)
    finally:
        await watcher.stop()


def test_watcher_rejects_empty_path():
    cfg = load_from_dict({"selectors": {}})
    reg = SelectorRegistry(cfg, storage_overrides={})
    with pytest.raises(ValueError, match="non-empty"):
        ConfigReloadWatcher(reg, "")
