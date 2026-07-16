import mimetypes
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.api.multipart import clean_urls, read_attachments
from app.common.enums.disputes import DisputeReason
from app.core.exceptions import NotFoundException
from app.modules.users.permissions import get_current_user
from app.modules.disputes.schemas import (
    DisputeResolutionRequest,
    DisputeResponse,
)
from app.modules.disputes.schemas.trader import (
    DisputeTraderResponse,
)
from app.modules.disputes.service import DisputeService
from app.modules.receipts.schemas import ReceiptItem
from app.modules.users.models import User
from app.modules.users.permissions import require_admin, require_trader

router = APIRouter()


def _evidence_file_response(receipt) -> FileResponse:
    """Serve a dispute-evidence receipt as a download (mirrors the order-receipt
    download). The caller has already resolved + authorised the receipt."""
    if not receipt.file_path or not os.path.exists(receipt.file_path):
        raise NotFoundException("Evidence file not found on disk")
    media_type, _ = mimetypes.guess_type(receipt.file_path)
    return FileResponse(
        path=receipt.file_path,
        media_type=media_type or "application/octet-stream",
        filename=os.path.basename(receipt.file_path),
    )


# ── Trader Endpoints ──────────────────────────────────────────────────────────

@router.get(
    "/my",
    response_model=List[DisputeTraderResponse],
    dependencies=[Depends(require_trader)],
    summary="List disputes for trader",
    description="Returns all disputes for orders assigned to the current trader.",
)
async def list_my_disputes(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    status: Optional[str] = Query(None, description="Dispute status"),
    reason: Optional[str] = Query(None, description="Dispute reason"),
    payment_method: Optional[str] = Query(None, description="Order payment method"),
    id_search: Optional[str] = Query(None, description="Search by order id/uuid/external_id"),
    amount_from: Optional[float] = Query(None, ge=0),
    amount_to: Optional[float] = Query(None, ge=0),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = DisputeService(session)
    return await service.list_trader_disputes(
        current_user.id,
        status=status,
        reason=reason,
        payment_method=payment_method,
        id_search=id_search,
        amount_from=amount_from,
        amount_to=amount_to,
        skip=skip,
        limit=limit,
    )


@router.get(
    "/my/{dispute_uuid}",
    response_model=DisputeTraderResponse,
    dependencies=[Depends(require_trader)],
    summary="Get dispute detail for trader",
)
async def get_my_dispute(
    dispute_uuid: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = DisputeService(session)
    return await service.get_trader_dispute_detail(current_user.id, dispute_uuid)


@router.post(
    "/my/{dispute_uuid}/accept",
    response_model=DisputeTraderResponse,
    dependencies=[Depends(require_trader)],
    summary="Trader accepts dispute (concede → merchant favour)",
    description=(
        "The trader agrees with the merchant: the dispute is resolved in the "
        "merchant's favour and the order becomes SUCCESS. Self-service — works "
        "only on the trader's own OPEN disputes."
    ),
)
async def accept_my_dispute(
    dispute_uuid: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = DisputeService(session)
    await service.trader_accept_dispute(current_user.id, dispute_uuid)
    # Return the order-enriched detail (same shape as GET /my/{uuid}) so the
    # client keeps the order fields after the action.
    return await service.get_trader_dispute_detail(current_user.id, dispute_uuid)


@router.post(
    "/my/{dispute_uuid}/reject",
    response_model=DisputeTraderResponse,
    dependencies=[Depends(require_trader)],
    summary="Trader rejects dispute (decide in own favour → order FAILED)",
    description=(
        "The trader declines the merchant's claim: the dispute is REJECTED in the "
        "trader's favour and the order becomes FAILED (frozen collateral released "
        "back to the trader). Self-service — works only on the trader's own OPEN "
        "disputes. The admin keeps resolve/reject as an override."
    ),
)
async def reject_my_dispute(
    dispute_uuid: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = DisputeService(session)
    await service.trader_reject_dispute(current_user.id, dispute_uuid)
    # Return the order-enriched detail (same shape as GET /my/{uuid}).
    return await service.get_trader_dispute_detail(current_user.id, dispute_uuid)


@router.post(
    "/my/{dispute_uuid}/request-proof",
    response_model=DisputeTraderResponse,
    dependencies=[Depends(require_trader)],
    summary="Trader asks the merchant for stronger proof (video / PDF)",
    description=(
        "Instead of deciding now, the trader asks the merchant for a video or a "
        "PDF check. The dispute stays OPEN (substatus video/pdf requested) and the "
        "merchant is nudged for the proof; once re-submitted it goes through "
        "premoderation and surfaces back to the trader. Works only on the trader's "
        "own OPEN disputes."
    ),
)
async def request_proof_my_dispute(
    dispute_uuid: str,
    kind: str = Query(..., regex="^(video|pdf)$", description="Proof type to request"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = DisputeService(session)
    await service.trader_request_proof(current_user.id, dispute_uuid, kind)
    # Return the order-enriched detail (same shape as GET /my/{uuid}).
    return await service.get_trader_dispute_detail(current_user.id, dispute_uuid)


@router.get(
    "/my/{dispute_uuid}/evidence",
    response_model=List[ReceiptItem],
    dependencies=[Depends(require_trader)],
    summary="List dispute evidence files (trader)",
    description="Premoderation-visible evidence only. Scoped to the trader's own disputes.",
)
async def list_my_dispute_evidence(
    dispute_uuid: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = DisputeService(session)
    rows = await service.list_evidence_trader(current_user.id, dispute_uuid)
    return [ReceiptItem.from_model(r) for r in rows]


@router.get(
    "/my/{dispute_uuid}/evidence/{receipt_uuid}",
    dependencies=[Depends(require_trader)],
    summary="Download a dispute evidence file (trader)",
    response_class=FileResponse,
)
async def download_my_dispute_evidence(
    dispute_uuid: str,
    receipt_uuid: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = DisputeService(session)
    receipt = await service.get_evidence_receipt_trader(
        current_user.id, dispute_uuid, receipt_uuid
    )
    return _evidence_file_response(receipt)


@router.get(
    "",
    response_model=List[DisputeResponse],
    dependencies=[Depends(require_admin)],
    summary="List all disputes (Admin)",
)
async def list_disputes(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    id_search: Optional[str] = Query(None, description="Search by order id/uuid/external_id"),
    trader_login: Optional[str] = Query(None, description="Filter by trader login"),
    merchant_login: Optional[str] = Query(None, description="Filter by merchant login"),
    status: Optional[str] = Query(None, description="Dispute status"),
    reason: Optional[str] = Query(None, description="Dispute reason"),
    payment_method: Optional[str] = Query(None, description="Order payment method"),
    amount_from: Optional[float] = Query(None, ge=0, description="Minimum order amount"),
    amount_to: Optional[float] = Query(None, ge=0, description="Maximum order amount"),
    session: AsyncSession = Depends(get_db),
):
    """
    Admin lists all disputes with optional filters and enriched response.
    """
    service = DisputeService(session)
    return await service.list_admin(
        skip=skip,
        limit=limit,
        id_search=id_search,
        trader_login=trader_login,
        merchant_login=merchant_login,
        status=status,
        reason=reason,
        payment_method=payment_method,
        amount_from=amount_from,
        amount_to=amount_to,
    )


@router.post(
    "",
    response_model=DisputeResponse,
    status_code=201,
    dependencies=[Depends(require_admin)],
    summary="Open dispute (Admin)",
)
async def open_dispute_admin(
    order_uuid: str = Form(..., description="Order UUID to dispute"),
    reason: DisputeReason = Form(...),
    attachments: List[UploadFile] = File(default=[]),
    evidence_urls: List[str] = Form(default=[]),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Admin opens a dispute on any order by its UUID (multipart/form-data).
    Optional evidence: files (``attachments``) and/or links (``evidence_urls``)."""
    service = DisputeService(session)
    return await service.open_dispute_by_admin(
        admin_id=current_user.id,
        reason=reason,
        order_uuid=order_uuid,
        attachments=await read_attachments(attachments),
        evidence_urls=clean_urls(evidence_urls),
    )


@router.post(
    "/{dispute_id}/resolve",
    response_model=DisputeResponse,
    dependencies=[Depends(require_admin)],
    summary="Resolve dispute (Admin)",
)
async def resolve_dispute(
    dispute_id: int,
    data: DisputeResolutionRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Resolve dispute in favor of the merchant.
    Order becomes SUCCESS. Funds are transferred to merchant.
    """
    service = DisputeService(session)
    return await service.resolve_dispute(
        admin_id=current_user.id, 
        dispute_id=dispute_id, 
        resolution_text=data.resolution_text
    )


@router.post(
    "/{dispute_id}/reject",
    response_model=DisputeResponse,
    dependencies=[Depends(require_admin)],
    summary="Reject dispute (Admin)",
)
async def reject_dispute(
    dispute_id: int,
    data: DisputeResolutionRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """
    Reject dispute (in favor of trader / client).
    Order becomes FAILED. Funds are returned to trader.
    """
    service = DisputeService(session)
    return await service.reject_dispute(
        admin_id=current_user.id,
        dispute_id=dispute_id,
        resolution_text=data.resolution_text
    )


@router.get(
    "/{dispute_id}/evidence",
    response_model=List[ReceiptItem],
    dependencies=[Depends(require_admin)],
    summary="List dispute evidence files (Admin)",
)
async def list_dispute_evidence_admin(
    dispute_id: int,
    session: AsyncSession = Depends(get_db),
):
    service = DisputeService(session)
    rows = await service.list_evidence_admin(dispute_id)
    return [ReceiptItem.from_model(r) for r in rows]


@router.get(
    "/{dispute_id}/evidence/{receipt_uuid}",
    dependencies=[Depends(require_admin)],
    summary="Download a dispute evidence file (Admin)",
    response_class=FileResponse,
)
async def download_dispute_evidence_admin(
    dispute_id: int,
    receipt_uuid: str,
    session: AsyncSession = Depends(get_db),
):
    service = DisputeService(session)
    receipt = await service.get_evidence_receipt_admin(dispute_id, receipt_uuid)
    return _evidence_file_response(receipt)
