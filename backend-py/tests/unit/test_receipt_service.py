"""Unit tests for ReceiptService (Phase 1 of multi-receipt).

The money-path-adjacent logic here is dedup (never store the same file twice
on an order) and the per-order cap; both are asserted explicitly.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.common.enums.receipt_moderations import ModerationStatus
from app.common.enums.receipts import ReceiptSource, ReceiptUploader
from app.modules.receipts.exceptions import DuplicateReceiptError, ReceiptLimitReachedError
from app.modules.receipts.service import ReceiptService


def _svc():
    svc = ReceiptService(session=MagicMock())
    svc.repository = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_add_receipt_creates_row_with_fields():
    svc = _svc()
    svc.repository.find_by_sha_for_order = AsyncMock(return_value=None)
    svc.repository.count_for_order = AsyncMock(return_value=0)
    created = MagicMock()
    svc.repository.create = AsyncMock(return_value=created)

    out = await svc.add_receipt(
        order_id=15, file_path="uploads/receipts/x.pdf",
        source=ReceiptSource.MERCHANT_API, uploaded_by="merchant",
        sha256="deadbeef", dispute_id=None,
    )
    assert out is created
    payload = svc.repository.create.await_args.args[0]
    assert payload["order_id"] == 15
    assert payload["file_path"] == "uploads/receipts/x.pdf"
    assert payload["source"] == ReceiptSource.MERCHANT_API
    assert payload["sha256"] == "deadbeef"
    assert payload["moderation_status"] == ModerationStatus.NONE


@pytest.mark.asyncio
async def test_add_receipt_rejects_duplicate_sha_on_same_order():
    svc = _svc()
    svc.repository.find_by_sha_for_order = AsyncMock(return_value=MagicMock())  # already there
    svc.repository.create = AsyncMock()

    with pytest.raises(DuplicateReceiptError):
        await svc.add_receipt(
            order_id=15, file_path="x.pdf",
            source=ReceiptSource.MERCHANT_API, sha256="deadbeef",
        )
    svc.repository.create.assert_not_called()


@pytest.mark.asyncio
async def test_add_receipt_enforces_per_order_cap():
    svc = _svc()
    svc.repository.find_by_sha_for_order = AsyncMock(return_value=None)
    svc.repository.count_for_order = AsyncMock(return_value=ReceiptService.MAX_PER_ORDER)
    svc.repository.create = AsyncMock()

    with pytest.raises(ReceiptLimitReachedError):
        await svc.add_receipt(
            order_id=15, file_path="x.pdf", source=ReceiptSource.MERCHANT_API,
        )
    svc.repository.create.assert_not_called()


@pytest.mark.asyncio
async def test_add_receipt_without_sha_skips_dedup():
    svc = _svc()
    svc.repository.find_by_sha_for_order = AsyncMock(return_value=MagicMock())
    svc.repository.count_for_order = AsyncMock(return_value=0)
    svc.repository.create = AsyncMock(return_value=MagicMock())

    await svc.add_receipt(order_id=15, file_path="x.pdf", source=ReceiptSource.SYSTEM)
    # No sha → dedup lookup not consulted, create proceeds.
    svc.repository.find_by_sha_for_order.assert_not_called()
    svc.repository.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_for_order_passes_visibility_flag():
    svc = _svc()
    svc.repository.list_for_order = AsyncMock(return_value=["r1"])
    out = await svc.list_for_order(15, visible_to_trader_only=True)
    assert out == ["r1"]
    svc.repository.list_for_order.assert_awaited_once_with(15, visible_to_trader_only=True)


# ── ReceiptUploader → (ReceiptSource, actor) — one upload channel maps to the
# fine source + the coarse actor stamped on the receipt row. ──


@pytest.mark.parametrize("uploader,exp_source,exp_actor", [
    (ReceiptUploader.MERCHANT, "merchant_api", "merchant"),
    (ReceiptUploader.MERCHANT_DISPUTE_BOT, "dispute_bot", "merchant"),
    (ReceiptUploader.MERCHANT_WEB, "merchant_web", "merchant"),
    (ReceiptUploader.SYSTEM, "system", "system"),
    (ReceiptUploader.TRADER, "trader", "trader"),
])
def test_receipt_uploader_channel_mapping(uploader, exp_source, exp_actor):
    assert uploader.source.value == exp_source
    assert uploader.actor == exp_actor
