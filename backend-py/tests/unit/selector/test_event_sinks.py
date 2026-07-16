"""Event sink contract tests + BufferedSink behavior."""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.modules.selector.logging_ import (
    BufferedSink,
    DecisionEvent,
    FeedbackEvent,
    LoggingSink,
    NullSink,
)


def _decision() -> DecisionEvent:
    return DecisionEvent(
        ts=1.0,
        selector_name="t",
        order_id="o",
        chosen_entity_id="e",
        candidates=[("e", 0.5, 0.4, 0.45, 1.0)],
        reason="selected",
        context={"x": 1},
    )


def _feedback() -> FeedbackEvent:
    return FeedbackEvent(
        ts=1.0,
        selector_name="t",
        order_id="o",
        entity_id="e",
        reward=1.0,
        signal="completed",
    )


def test_decision_event_to_dict_shape():
    d = _decision()
    payload = d.to_dict()
    assert payload["selector_name"] == "t"
    assert payload["order_id"] == "o"
    assert payload["chosen_entity_id"] == "e"
    assert isinstance(payload["candidates"], list)
    assert payload["candidates"][0]["entity_id"] == "e"
    assert payload["candidates"][0]["probability"] == 1.0
    assert payload["reason"] == "selected"
    assert payload["experiment_variant"] == ""


def test_feedback_event_to_dict_shape():
    f = _feedback()
    d = f.to_dict()
    assert d["signal"] == "completed"
    assert d["duplicate"] is False
    assert d["reward"] == 1.0


@pytest.mark.asyncio
async def test_null_sink_drops_everything():
    sink = NullSink()
    await sink.emit_decision(_decision())
    await sink.emit_feedback(_feedback())
    await sink.close()  # no-op


@pytest.mark.asyncio
async def test_logging_sink_emits_via_python_logger(caplog):
    sink = LoggingSink(log=logging.getLogger("test_sink"))
    with caplog.at_level(logging.INFO, logger="test_sink"):
        await sink.emit_decision(_decision())
        await sink.emit_feedback(_feedback())
    msgs = [r.message for r in caplog.records]
    assert "selector_decision" in msgs
    assert "selector_feedback" in msgs


@pytest.mark.asyncio
async def test_buffered_sink_forwards_in_order():
    seen: list[str] = []

    class _Recorder:
        async def emit_decision(self, ev):
            seen.append(f"D:{ev.order_id}")

        async def emit_feedback(self, ev):
            seen.append(f"F:{ev.order_id}")

        async def close(self):
            return None

    buf = BufferedSink(_Recorder(), max_queue=10)
    try:
        for i in range(3):
            await buf.emit_decision(
                DecisionEvent(
                    ts=1.0,
                    selector_name="t",
                    order_id=f"o{i}",
                    chosen_entity_id="e",
                    candidates=[],
                    reason="selected",
                    context={},
                )
            )
        await buf.emit_feedback(
            FeedbackEvent(ts=1.0, selector_name="t", order_id="o-fb", entity_id="e", reward=1.0, signal="completed")
        )
    finally:
        await buf.close()
    assert seen[0:3] == ["D:o0", "D:o1", "D:o2"]
    assert seen[3] == "F:o-fb"


@pytest.mark.asyncio
async def test_buffered_sink_drops_oldest_when_full():
    """Bounded queue must drop oldest rather than blocking the producer."""

    class _SlowSink:
        def __init__(self):
            self.released = asyncio.Event()
            self.processed = 0

        async def emit_decision(self, ev):
            await self.released.wait()
            self.processed += 1

        async def emit_feedback(self, ev):
            await self.released.wait()

        async def close(self):
            return None

    slow = _SlowSink()
    buf = BufferedSink(slow, max_queue=2)
    try:
        # Stuff in 5 events; queue can only hold 2 (plus 1 in-flight).
        for i in range(5):
            await buf.emit_decision(
                DecisionEvent(
                    ts=1.0,
                    selector_name="t",
                    order_id=f"o{i}",
                    chosen_entity_id=None,
                    candidates=[],
                    reason="no_candidates",
                    context={},
                )
            )
        assert buf.dropped > 0
        slow.released.set()
    finally:
        await buf.close()


@pytest.mark.asyncio
async def test_buffered_sink_swallows_inner_exceptions(caplog):
    class _Broken:
        async def emit_decision(self, ev):
            raise RuntimeError("boom")

        async def emit_feedback(self, ev):
            raise RuntimeError("boom")

        async def close(self):
            return None

    buf = BufferedSink(_Broken(), max_queue=5)
    try:
        with caplog.at_level(logging.WARNING):
            await buf.emit_decision(
                DecisionEvent(
                    ts=1.0,
                    selector_name="t",
                    order_id="o",
                    chosen_entity_id=None,
                    candidates=[],
                    reason="no_candidates",
                    context={},
                )
            )
    finally:
        await buf.close()
    assert any("selector_event_sink_failed" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_buffered_sink_ignores_after_close():
    sink = NullSink()
    buf = BufferedSink(sink, max_queue=5)
    await buf.close()
    # Post-close emits are silently dropped.
    await buf.emit_decision(_decision())
    await buf.emit_feedback(_feedback())
