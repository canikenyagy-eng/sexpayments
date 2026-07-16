from typing import List

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.api.dependencies import get_service
from app.api.merchant.dependencies import get_current_merchant
from app.api.multipart import clean_urls, read_attachments
from app.common.enums.disputes import DisputeReason
from app.common.enums.receipts import ReceiptUploader
from app.modules.disputes.schemas import (
    DisputeCreate,
    DisputeMerchantResponse,
)
from app.modules.disputes.service import DisputeService
from app.modules.merchants.models import Merchant

router = APIRouter()


@router.get(
    "",
    response_model=list[DisputeMerchantResponse],
    summary="List merchant disputes",
)
async def list_disputes(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    merchant: Merchant = Depends(get_current_merchant),
    dispute_service: DisputeService = Depends(get_service(DisputeService)),
):
    """List all disputes for the authenticated merchant."""
    return await dispute_service.list_merchant_disputes(merchant.id, skip=skip, limit=limit)


@router.post(
    "/by-order/{order_id}",
    response_model=DisputeMerchantResponse,
    status_code=201,
    summary="Open dispute by order UUID",
)
async def create_dispute(
    order_id: str,
    reason: DisputeReason = Form(...),
    attachments: List[UploadFile] = File(default=[]),
    evidence_urls: List[str] = Form(default=[]),
    merchant: Merchant = Depends(get_current_merchant),
    dispute_service: DisputeService = Depends(get_service(DisputeService)),
):
    """Open a dispute for a specific order by its UUID (multipart/form-data).

    Optional evidence: upload one or more files (``attachments``) and/or pass
    links we download server-side (``evidence_urls``). Every file is format-
    checked (images/PDF/video) and goes through receipt premoderation.

    If a dispute already exists for this order and is still open, the call is
    additive: the new evidence is appended to it (and the same dispute returned)
    rather than rejected — so you can keep attaching checks via this endpoint.
    """
    return await dispute_service.open_dispute_by_merchant(
        merchant,
        DisputeCreate(reason=reason, evidence_files=[]),
        order_id=order_id,
        attachments=await read_attachments(attachments),
        evidence_urls=clean_urls(evidence_urls),
    )


@router.post(
    "/by-external/{external_id}",
    response_model=DisputeMerchantResponse,
    status_code=201,
    summary="Open dispute by external order ID",
)
async def create_dispute_by_external_id(
    external_id: str,
    reason: DisputeReason = Form(...),
    attachments: List[UploadFile] = File(default=[]),
    evidence_urls: List[str] = Form(default=[]),
    merchant: Merchant = Depends(get_current_merchant),
    dispute_service: DisputeService = Depends(get_service(DisputeService)),
):
    """Open a dispute for a specific order by its external ID (multipart/form-data).
    Same evidence options as ``/by-order``."""
    return await dispute_service.open_dispute_by_merchant(
        merchant,
        DisputeCreate(reason=reason, evidence_files=[]),
        external_id=external_id,
        attachments=await read_attachments(attachments),
        evidence_urls=clean_urls(evidence_urls),
    )


@router.get(
    "/{dispute_uuid}",
    response_model=DisputeMerchantResponse,
    summary="Get dispute details",
)
async def get_dispute(
    dispute_uuid: str,
    merchant: Merchant = Depends(get_current_merchant),
    dispute_service: DisputeService = Depends(get_service(DisputeService)),
):
    """Get dispute details."""
    return await dispute_service.get_merchant_dispute_detail(merchant, dispute_uuid)


@router.post(
    "/{dispute_uuid}/receipt",
    response_model=DisputeMerchantResponse,
    summary="Add a receipt to an open dispute",
)
async def add_dispute_receipt(
    dispute_uuid: str,
    attachment: UploadFile = File(..., description="Receipt file / photo (image or PDF)"),
    merchant: Merchant = Depends(get_current_merchant),
    dispute_service: DisputeService = Depends(get_service(DisputeService)),
):
    """Attach another receipt to one of your OPEN disputes — e.g. to fulfil a
    ``pdf_requested`` / ``video_requested`` proof request (see the dispute
    ``substatus`` in the webhook). The file is format-checked, goes through
    premoderation and is added to the dispute's evidence. Works only while the
    dispute is open."""
    return await dispute_service.add_merchant_evidence(
        merchant, dispute_uuid, attachment, uploaded_by=ReceiptUploader.MERCHANT,
    )
