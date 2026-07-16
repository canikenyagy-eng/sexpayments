"""Pluggable event sinks for select/feedback decisions.

Kept deliberately small:

  - ``NullSink``       — drops everything (tests, dev).
  - ``LoggingSink``    — structured JSON via python logger. Default; whatever
                         shipper you use (vector, fluentbit, journald → CH)
                         picks it up from there.
  - ``BufferedSink``   — decorator that decouples emit from the actual write,
                         so a slow downstream sink can't backpressure the
                         hot path.

There is no Kafka sink — overkill for this scale. When/if ClickHouse is
wired up, add a ``ClickHouseSink`` that batches inserts against the tables
in ``app/infrastructure/clickhouse/schema/selector.sql``; it slots in via the same ``EventSink``
Protocol with no changes to the engine.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Protocol

from app.modules.selector.logging_.events import DecisionEvent, FeedbackEvent


logger = logging.getLogger("selector.events")


class EventSink(Protocol):
    async def emit_decision(self, event: DecisionEvent) -> None: ...

    async def emit_feedback(self, event: FeedbackEvent) -> None: ...

    async def close(self) -> None: ...


class NullSink:
    """Drops every event. Default for unit tests."""

    async def emit_decision(self, event: DecisionEvent) -> None:
        return None

    async def emit_feedback(self, event: FeedbackEvent) -> None:
        return None

    async def close(self) -> None:
        return None


class LoggingSink:
    """Emits events as structured log records.

    The records carry ``event="decision"``/``event="feedback"`` and a
    ``payload`` dict. A log shipper (vector / fluentbit / promtail) can route
    them to ClickHouse / S3 / wherever without any in-process dependency.
    """

    def __init__(self, log: Optional[logging.Logger] = None):
        self._log = log or logger

    async def emit_decision(self, event: DecisionEvent) -> None:
        self._log.info(
            "selector_decision",
            extra={"event": "decision", "payload": event.to_dict()},
        )

    async def emit_feedback(self, event: FeedbackEvent) -> None:
        self._log.info(
            "selector_feedback",
            extra={"event": "feedback", "payload": event.to_dict()},
        )

    async def close(self) -> None:
        return None


class BufferedSink:
    """Drop-in wrapper that decouples producers from a slow inner sink.

    The hot path (``emit_decision`` / ``emit_feedback``) just enqueues to a
    bounded ``asyncio.Queue``. A background drain task pushes to the inner
    sink. If the queue is full, the oldest event is dropped and counted so
    a transient downstream stall doesn't ever block order processing.

    Call ``close()`` on shutdown to flush and stop the drain task.
    """

    def __init__(
        self,
        inner: EventSink,
        *,
        max_queue: int = 10_000,
        drain_concurrency: int = 1,
    ):
        self._inner = inner
        self._queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=max_queue)
        self._closed = False
        self._dropped = 0
        self._workers = [
            asyncio.create_task(self._drain()) for _ in range(drain_concurrency)
        ]

    @property
    def dropped(self) -> int:
        return self._dropped

    async def emit_decision(self, event: DecisionEvent) -> None:
        self._enqueue(("decision", event))

    async def emit_feedback(self, event: FeedbackEvent) -> None:
        self._enqueue(("feedback", event))

    def _enqueue(self, item: Any) -> None:
        if self._closed:
            return
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            try:
                self._queue.get_nowait()
                # task_done() must balance the put that originally enqueued
                # this item, otherwise queue.join() in close() blocks forever.
                self._queue.task_done()
                self._dropped += 1
                self._queue.put_nowait(item)
            except asyncio.QueueEmpty:
                self._dropped += 1

    async def _drain(self) -> None:
        while True:
            try:
                kind, ev = await self._queue.get()
            except asyncio.CancelledError:
                return
            try:
                if kind == "decision":
                    await self._inner.emit_decision(ev)
                else:
                    await self._inner.emit_feedback(ev)
            except Exception as exc:
                logger.warning("selector_event_sink_failed: %s", exc)
            finally:
                self._queue.task_done()

    async def close(self) -> None:
        self._closed = True
        await self._queue.join()
        for w in self._workers:
            w.cancel()
        for w in self._workers:
            try:
                await w
            except (asyncio.CancelledError, Exception):
                pass
        await self._inner.close()
