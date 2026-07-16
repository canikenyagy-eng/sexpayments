"""The shared sync ClickHouse client is NOT thread-safe, yet stats reads run on
the default executor pool and the batch sink inserts on its own thread. Without
serialization they collide on the one client → corrupted queries → the fail-safe
read returns empty ("table loads через раз"). These tests pin the serialization.
"""
import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest


class _ConcurrencyTracker:
    def __init__(self):
        self.current = 0
        self.max = 0
        self._lock = threading.Lock()

    def enter(self):
        with self._lock:
            self.current += 1
            self.max = max(self.max, self.current)

    def leave(self):
        with self._lock:
            self.current -= 1


def _fake_client(tracker: _ConcurrencyTracker):
    class _Res:
        column_names: list = []
        result_rows: list = []

    class _Client:
        def query(self, sql, parameters=None):
            tracker.enter()
            time.sleep(0.02)  # simulate the CH round-trip window
            tracker.leave()
            return _Res()

        def insert(self, table, rows, column_names=None):
            tracker.enter()
            time.sleep(0.02)
            tracker.leave()

    return _Client()


@pytest.mark.asyncio
async def test_concurrent_ch_reads_are_serialized():
    from app.modules.audit import repository as audit_repo

    tracker = _ConcurrencyTracker()
    with patch(
        "app.infrastructure.clickhouse.client.get_client",
        return_value=_fake_client(tracker),
    ), patch(
        "app.modules.audit.repository.get_settings",
        return_value=SimpleNamespace(CLICKHOUSE_ENABLED=True),
    ):
        await asyncio.gather(*[audit_repo._fetch("SELECT 1", {}) for _ in range(8)])

    # Not thread-safe → accesses must serialize. Without the lock this would be > 1.
    assert tracker.max == 1


@pytest.mark.asyncio
async def test_activity_ch_reads_are_serialized():
    from app.modules.stats import activity_ch

    tracker = _ConcurrencyTracker()
    with patch(
        "app.infrastructure.clickhouse.client.get_client",
        return_value=_fake_client(tracker),
    ), patch(
        "app.modules.stats.activity_ch.get_settings",
        return_value=SimpleNamespace(CLICKHOUSE_ENABLED=True),
    ):
        await asyncio.gather(*[activity_ch._fetch("SELECT 1", {}) for _ in range(8)])

    assert tracker.max == 1
