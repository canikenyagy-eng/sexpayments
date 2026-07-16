"""Generic ClickHouse delivery sink — batched, off the event loop, fail-safe.

Domain records (provider request/callback, merchant API log, snapshot) live in
their module repositories; each carries ``.table`` / ``.columns`` / ``.as_row()``
and is routed here for delivery. Shared by every CH-backed logger.

  * Web process: ``AsyncBatchSink`` buffers and flushes in batches via a thread
    executor (the sync CH client never blocks the loop). Never blocks the hot path.
  * Celery / background: ``SyncDirectSink`` inserts immediately (low volume).
  * CH disabled: ``NullProviderSink`` drops everything.

Any ClickHouse error is logged and dropped — logging must never break or slow a
caller. Records are grouped by ``.table`` on flush so different record types can
share one sink.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Protocol

from app.core.config import get_settings
from app.infrastructure.clickhouse import client as ch

logger = logging.getLogger("infrastructure.clickhouse.sink")

# Shared truncation limits for CH string payloads.
MAX_BODY = 32_768
MAX_ERROR = 2_048


def _insert_rows(table: str, columns: List[str], rows: List[list]) -> None:
    """Blocking multi-row insert. Run via executor (web) or directly (Celery).

    Serialized through ``ch.query_lock`` so a background flush never runs on the
    shared sync client concurrently with a stats read (the client is not
    thread-safe; concurrent use corrupts both)."""
    if not rows:
        return
    with ch.query_lock:
        ch.get_client().insert(table, rows, column_names=columns)


class RecordSink(Protocol):
    def emit(self, rec: Any) -> None: ...
    async def aclose(self) -> None: ...


class NullProviderSink:
    """Drops everything (CH disabled / tests / dev)."""

    def emit(self, rec: Any) -> None:
        return None

    async def aclose(self) -> None:
        return None


class AsyncBatchSink:
    """Web process: buffer records, flush in batches off the event loop.

    ``emit`` is sync + non-blocking (append). A background task flushes every
    ``flush_interval_s``; a full buffer triggers an extra flush. Inserts run in
    a thread executor so the (sync) CH client never blocks the loop. Bounded
    buffer drops the oldest on overflow so a CH stall can't grow memory.
    """

    def __init__(self, *, batch_size: int = 500, flush_interval_s: float = 2.0, max_buffer: int = 50_000):
        self._buf: List[Any] = []
        self._batch_size = batch_size
        self._flush_interval_s = flush_interval_s
        self._max_buffer = max_buffer
        self._dropped = 0
        self._closed = False
        self._lock = asyncio.Lock()
        self._task = asyncio.create_task(self._run())

    def emit(self, rec: Any) -> None:
        if self._closed:
            return
        if len(self._buf) >= self._max_buffer:
            self._buf.pop(0)
            self._dropped += 1
        self._buf.append(rec)
        if len(self._buf) >= self._batch_size:
            try:
                asyncio.get_running_loop().create_task(self._flush())
            except RuntimeError:
                pass  # no loop (shouldn't happen in web); periodic will catch it

    async def _run(self) -> None:
        try:
            while not self._closed:
                await asyncio.sleep(self._flush_interval_s)
                await self._flush()
        except asyncio.CancelledError:
            return

    async def _flush(self) -> None:
        async with self._lock:
            if not self._buf:
                return
            batch, self._buf = self._buf, []
        groups: Dict[str, list] = {}
        for r in batch:
            groups.setdefault(r.table, []).append(r)
        loop = asyncio.get_running_loop()
        for table, recs in groups.items():
            try:
                rows = [r.as_row() for r in recs]
                await loop.run_in_executor(None, _insert_rows, table, recs[0].columns, rows)
            except Exception as exc:  # noqa: BLE001 — never break callers
                logger.warning("%s insert failed (%d rows dropped): %s", table, len(recs), exc)

    async def aclose(self) -> None:
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self._flush()


class SyncDirectSink:
    """Celery / background: insert immediately with the sync client (low
    volume; no event loop to schedule on). Fail-safe."""

    def emit(self, rec: Any) -> None:
        try:
            _insert_rows(rec.table, rec.columns, [rec.as_row()])
        except Exception as exc:  # noqa: BLE001 — never break the task
            logger.warning("%s direct insert failed: %s", rec.table, exc)

    async def aclose(self) -> None:
        return None


# ── Singleton + routing ────────────────────────────────────────────────────

_sink: RecordSink = NullProviderSink()


def init_sink(enabled: bool) -> None:
    """Web startup: install the batched async sink (or the null sink). Requires
    a running event loop (call from the FastAPI lifespan)."""
    global _sink
    _sink = AsyncBatchSink() if enabled else NullProviderSink()


def _sink_for_emit() -> RecordSink:
    """Return the active sink, lazily creating a Celery/background sync sink the
    first time a background process emits (no explicit init there)."""
    global _sink
    if isinstance(_sink, NullProviderSink) and get_settings().CLICKHOUSE_ENABLED:
        _sink = SyncDirectSink()
    return _sink


def emit_record(rec: Any) -> None:
    """Emit any record (carrying ``.table`` / ``.columns`` / ``.as_row()``) to
    the shared batched sink — routed to its own ClickHouse table on flush."""
    _sink_for_emit().emit(rec)


def emit_records(records: List[Any]) -> None:
    """Batched sibling of ``emit_record`` for bulk producers: group by ``.table``
    and write each group in ONE multi-row insert (never per-row). Synchronous +
    fail-safe — intended for background/Celery producers like the per-minute
    requisite-activity snapshot, where a whole tick must be a single insert. A
    no-op when ClickHouse is disabled; CH errors are logged and dropped."""
    if not records or not get_settings().CLICKHOUSE_ENABLED:
        return
    groups: Dict[str, list] = {}
    for r in records:
        groups.setdefault(r.table, []).append(r)
    for table, recs in groups.items():
        try:
            _insert_rows(table, recs[0].columns, [r.as_row() for r in recs])
        except Exception as exc:  # noqa: BLE001 — never break the caller
            logger.warning("%s batch insert failed (%d rows dropped): %s", table, len(recs), exc)


async def aclose_sink() -> None:
    global _sink
    sink, _sink = _sink, NullProviderSink()
    await sink.aclose()
