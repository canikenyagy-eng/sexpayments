"""Transport-level tests for the merchant dispute-open endpoints (multipart).

The endpoints are thin: read the uploaded files' bytes, drop blank URL fields,
and delegate the whole lifecycle to DisputeService.open_dispute_by_merchant.
"""
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.datastructures import UploadFile

from app.common.enums.disputes import DisputeReason

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(filename=name, file=BytesIO(content))


@pytest.mark.asyncio
async def test_create_dispute_reads_files_and_delegates():
    from app.api.merchant.v1.endpoints.disputes import create_dispute

    svc = MagicMock(open_dispute_by_merchant=AsyncMock(return_value="DISPUTE"))
    out = await create_dispute(
        "order-uuid",
        reason=DisputeReason.NO_PAYMENT,
        attachments=[_upload("p.png", _PNG)],
        evidence_urls=["https://cdn/x.png", "   "],   # blank dropped
        merchant=MagicMock(),
        dispute_service=svc,
    )

    assert out == "DISPUTE"
    kw = svc.open_dispute_by_merchant.await_args.kwargs
    assert kw["order_id"] == "order-uuid"
    assert kw["attachments"] == [(_PNG, "p.png")]
    assert kw["evidence_urls"] == ["https://cdn/x.png"]


@pytest.mark.asyncio
async def test_create_dispute_by_external_delegates_with_external_id():
    from app.api.merchant.v1.endpoints.disputes import create_dispute_by_external_id

    svc = MagicMock(open_dispute_by_merchant=AsyncMock(return_value="DISPUTE"))
    await create_dispute_by_external_id(
        "ext-1",
        reason=DisputeReason.INVALID_SUM,
        attachments=[],
        evidence_urls=[],
        merchant=MagicMock(),
        dispute_service=svc,
    )

    kw = svc.open_dispute_by_merchant.await_args.kwargs
    assert kw["external_id"] == "ext-1"
    assert kw["attachments"] == [] and kw["evidence_urls"] == []


@pytest.mark.asyncio
async def test_admin_open_dispute_reads_files_and_delegates():
    from app.api.v1.endpoints.disputes import open_dispute_admin

    with patch("app.api.v1.endpoints.disputes.DisputeService") as DS:
        DS.return_value.open_dispute_by_admin = AsyncMock(return_value="DISPUTE")
        out = await open_dispute_admin(
            order_uuid="order-uuid",
            reason=DisputeReason.NO_PAYMENT,
            attachments=[_upload("p.png", _PNG)],
            evidence_urls=["https://cdn/x.png", "  "],
            current_user=MagicMock(id=42),
            session=MagicMock(),
        )

    assert out == "DISPUTE"
    kw = DS.return_value.open_dispute_by_admin.await_args.kwargs
    assert kw["admin_id"] == 42
    assert kw["order_uuid"] == "order-uuid"
    assert kw["attachments"] == [(_PNG, "p.png")]
    assert kw["evidence_urls"] == ["https://cdn/x.png"]


@pytest.mark.asyncio
async def test_cabinet_open_dispute_reads_files_and_delegates():
    from app.api.v1.endpoints.merchants import open_my_dispute

    msvc = MagicMock(get_merchant_for_user=AsyncMock(return_value=MagicMock()))
    dsvc = MagicMock(open_dispute_by_merchant=AsyncMock(return_value="DISPUTE"))
    out = await open_my_dispute(
        order_uuid="order-uuid",
        reason=DisputeReason.INVALID_SUM,
        attachments=[_upload("p.png", _PNG)],
        evidence_urls=[],
        merchant_id=None,
        current_user=MagicMock(id=5),
        merchant_service=msvc,
        dispute_service=dsvc,
    )

    assert out == "DISPUTE"
    kw = dsvc.open_dispute_by_merchant.await_args.kwargs
    assert kw["order_id"] == "order-uuid"
    assert kw["attachments"] == [(_PNG, "p.png")]
    assert kw["evidence_urls"] == []


@pytest.mark.asyncio
async def test_create_dispute_drops_empty_file_parts():
    """A multipart part with no filename (some clients send one) is ignored."""
    from app.api.merchant.v1.endpoints.disputes import create_dispute

    svc = MagicMock(open_dispute_by_merchant=AsyncMock(return_value="D"))
    empty = UploadFile(filename="", file=BytesIO(b""))
    await create_dispute(
        "u", reason=DisputeReason.NO_PAYMENT,
        attachments=[empty, _upload("p.png", _PNG)],
        evidence_urls=[], merchant=MagicMock(), dispute_service=svc,
    )
    assert svc.open_dispute_by_merchant.await_args.kwargs["attachments"] == [(_PNG, "p.png")]
