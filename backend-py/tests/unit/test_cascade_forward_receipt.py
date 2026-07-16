"""Unit tests for the cascade receipt-forward worker (_forward_receipt_async).

Per-receipt routing: when a ``receipt_id`` is given the worker forwards THAT
receipt's file (not the order mirror), so concurrent multi-uploads each reach
the provider exactly once. Without one it falls back to ``order.receipt_file``
(historical behaviour).
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.workers.tasks import cascade as cascade_task


class _FakeSession:
    def __init__(self, receipt=None):
        self._receipt = receipt

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, model, pk):
        # Only Receipt lookups go through session.get in the worker.
        return self._receipt


def _patches(*, attempt, order, provider, adapter, session):
    attempt_repo = MagicMock(get_won_for_order=AsyncMock(return_value=attempt))
    order_repo = MagicMock(get=AsyncMock(return_value=order))
    provider_repo = MagicMock(get=AsyncMock(return_value=provider))
    return [
        patch.object(cascade_task, "SessionLocal", lambda: session),
        patch.object(cascade_task, "CascadeOrderAttemptRepository", return_value=attempt_repo),
        patch("app.modules.orders.repository.OrderRepository", return_value=order_repo),
        patch.object(cascade_task, "CascadeProviderRepository", return_value=provider_repo),
        patch.object(cascade_task.registry, "get", return_value=adapter),
    ]


@pytest.mark.asyncio
async def test_forward_receipt_uses_specific_receipt_path():
    attempt = MagicMock(external_order_id="EXT-1", provider_id=1)
    order = MagicMock(receipt_file="mirror_latest.pdf", trader_comment="c")
    provider = MagicMock(adapter_type="swifty")
    adapter = MagicMock(notify_receipt=AsyncMock(return_value=True))
    receipt = MagicMock(file_path="specific_receipt.pdf")

    ctxs = _patches(attempt=attempt, order=order, provider=provider, adapter=adapter,
                    session=_FakeSession(receipt=receipt))
    for c in ctxs:
        c.start()
    try:
        out = await cascade_task._forward_receipt_async(7, receipt_id=55)
    finally:
        for c in ctxs:
            c.stop()

    assert out == "sent"
    # Forwarded the SPECIFIC receipt, not the order mirror.
    assert adapter.notify_receipt.await_args.kwargs["receipt_path"] == "specific_receipt.pdf"


@pytest.mark.asyncio
async def test_forward_receipt_falls_back_to_mirror_without_receipt_id():
    attempt = MagicMock(external_order_id="EXT-1", provider_id=1)
    order = MagicMock(receipt_file="mirror_latest.pdf", trader_comment="c")
    provider = MagicMock(adapter_type="swifty")
    adapter = MagicMock(notify_receipt=AsyncMock(return_value=True))

    ctxs = _patches(attempt=attempt, order=order, provider=provider, adapter=adapter,
                    session=_FakeSession(receipt=None))
    for c in ctxs:
        c.start()
    try:
        out = await cascade_task._forward_receipt_async(7)  # no receipt_id
    finally:
        for c in ctxs:
            c.stop()

    assert out == "sent"
    assert adapter.notify_receipt.await_args.kwargs["receipt_path"] == "mirror_latest.pdf"
