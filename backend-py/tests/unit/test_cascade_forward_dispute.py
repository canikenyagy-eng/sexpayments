"""Unit tests for the cascade dispute-forward worker (_forward_dispute_async).

Regression focus: the worker must forward the dispute's evidence FILES (the
model field is ``evidence_files``, a list) and the reason's string value to
the provider adapter. The old code read a nonexistent ``evidence_file``
(singular) so evidence never reached the provider, and passed the reason as
an enum object.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.enums.disputes import DisputeReason
from app.workers.tasks import cascade as cascade_task


class _FakeSession:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return False


def _patch_pipeline(*, dispute, attempt, provider, adapter, receipts=None):
    """Patch SessionLocal + repos + registry used by _forward_dispute_async."""
    dispute_repo = MagicMock()
    dispute_repo.get = AsyncMock(return_value=dispute)
    attempt_repo = MagicMock()
    attempt_repo.get_won_for_order = AsyncMock(return_value=attempt)
    provider_repo = MagicMock()
    provider_repo.get = AsyncMock(return_value=provider)

    # Appeal-evidence receipts are unioned into the evidence; default to none so
    # the legacy ``evidence_files`` assertions stay focused (the union is covered
    # by its own test below).
    receipt_repo = MagicMock()
    receipt_repo.list_for_dispute = AsyncMock(return_value=list(receipts or []))

    return [
        patch.object(cascade_task, "SessionLocal", lambda: _FakeSession()),
        patch("app.modules.disputes.repository.DisputeRepository", return_value=dispute_repo),
        patch("app.modules.receipts.repository.ReceiptRepository", return_value=receipt_repo),
        patch.object(cascade_task, "CascadeOrderAttemptRepository", return_value=attempt_repo),
        patch.object(cascade_task, "CascadeProviderRepository", return_value=provider_repo),
        patch.object(cascade_task.registry, "get", return_value=adapter),
    ]


@pytest.mark.asyncio
async def test_forward_dispute_passes_evidence_files_and_reason_value():
    dispute = MagicMock()
    dispute.id = 1
    dispute.order_id = 7
    dispute.evidence_files = ["/uploads/a.png", "/uploads/b.pdf"]
    dispute.reason = DisputeReason.HAS_PAYMENT

    attempt = MagicMock()
    attempt.external_order_id = "EXT-123"
    attempt.provider_id = 9

    provider = MagicMock()
    provider.adapter_type = "swifty"

    adapter = MagicMock()
    adapter.raise_dispute = AsyncMock(return_value=True)

    ctxs = _patch_pipeline(dispute=dispute, attempt=attempt, provider=provider, adapter=adapter)
    for c in ctxs:
        c.start()
    try:
        outcome = await cascade_task._forward_dispute_async(1)
    finally:
        for c in ctxs:
            c.stop()

    assert outcome == "sent"
    adapter.raise_dispute.assert_awaited_once()
    kwargs = adapter.raise_dispute.await_args.kwargs
    assert kwargs["external_order_id"] == "EXT-123"
    # The fix: full list of evidence files forwarded (not dropped).
    assert kwargs["evidence_paths"] == ["/uploads/a.png", "/uploads/b.pdf"]
    # Reason forwarded as its string value, not the enum repr.
    assert kwargs["reason"] == "has_payment"


@pytest.mark.asyncio
async def test_forward_dispute_empty_evidence_is_empty_list():
    dispute = MagicMock()
    dispute.id = 2
    dispute.order_id = 8
    dispute.evidence_files = None  # nothing attached
    dispute.reason = DisputeReason.NO_PAYMENT

    attempt = MagicMock()
    attempt.external_order_id = "EXT-9"
    attempt.provider_id = 3

    provider = MagicMock()
    provider.adapter_type = "swifty"

    adapter = MagicMock()
    adapter.raise_dispute = AsyncMock(return_value=True)

    ctxs = _patch_pipeline(dispute=dispute, attempt=attempt, provider=provider, adapter=adapter)
    for c in ctxs:
        c.start()
    try:
        outcome = await cascade_task._forward_dispute_async(2)
    finally:
        for c in ctxs:
            c.stop()

    assert outcome == "sent"
    assert adapter.raise_dispute.await_args.kwargs["evidence_paths"] == []
    assert adapter.raise_dispute.await_args.kwargs["reason"] == "no_payment"


@pytest.mark.asyncio
async def test_forward_dispute_unions_appeal_evidence_receipts():
    """Receipts uploaded against the dispute (unified store) are unioned into
    the evidence, deduped, preserving order."""
    dispute = MagicMock()
    dispute.id = 3
    dispute.order_id = 9
    dispute.evidence_files = ["/uploads/a.png"]
    dispute.reason = DisputeReason.HAS_PAYMENT

    attempt = MagicMock(external_order_id="EXT-7", provider_id=1)
    provider = MagicMock(adapter_type="swifty")
    adapter = MagicMock()
    adapter.raise_dispute = AsyncMock(return_value=True)

    # Two receipts; one duplicates an existing evidence_files path → deduped.
    r1 = MagicMock(file_path="/uploads/a.png")   # dup
    r2 = MagicMock(file_path="/uploads/receipt2.pdf")

    ctxs = _patch_pipeline(
        dispute=dispute, attempt=attempt, provider=provider, adapter=adapter,
        receipts=[r1, r2],
    )
    for c in ctxs:
        c.start()
    try:
        await cascade_task._forward_dispute_async(3)
    finally:
        for c in ctxs:
            c.stop()

    assert adapter.raise_dispute.await_args.kwargs["evidence_paths"] == [
        "/uploads/a.png", "/uploads/receipt2.pdf",
    ]


@pytest.mark.asyncio
async def test_forward_dispute_no_won_attempt_short_circuits():
    """Non-cascade order (no won attempt) → no adapter call, returns no_attempt."""
    dispute = MagicMock()
    dispute.id = 3
    dispute.order_id = 11
    dispute.evidence_files = []
    dispute.reason = DisputeReason.UNKNOWN

    adapter = MagicMock()
    adapter.raise_dispute = AsyncMock(return_value=True)

    ctxs = _patch_pipeline(dispute=dispute, attempt=None, provider=None, adapter=adapter)
    for c in ctxs:
        c.start()
    try:
        outcome = await cascade_task._forward_dispute_async(3)
    finally:
        for c in ctxs:
            c.stop()

    assert outcome == "no_attempt"
    adapter.raise_dispute.assert_not_awaited()
