from typing import List, Optional

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_service
from app.modules.audit.schemas import AuditLogResponse, MerchantApiLogResponse, OrderCreationSnapshotResponse
from app.modules.audit.service import AuditService
from app.modules.users.permissions import require_admin

router = APIRouter()


@router.get(
    "/api-logs",
    response_model=List[MerchantApiLogResponse],
    summary="List merchant API request logs",
    dependencies=[Depends(require_admin)],
)
async def list_api_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    merchant_id: Optional[int] = Query(None),
    method: Optional[str] = Query(None),
    endpoint_group: Optional[str] = Query(None),
    sort_by: Optional[str] = Query(None),
    sort_order: Optional[str] = Query("desc"),
    audit_service: AuditService = Depends(get_service(AuditService)),
):
    return await audit_service.list_api_logs(
        skip=skip, limit=limit, merchant_id=merchant_id, method=method, endpoint_group=endpoint_group, sort_by=sort_by, sort_order=sort_order
    )


@router.get(
    "/api-logs/{request_id}/snapshot",
    response_model=Optional[OrderCreationSnapshotResponse],
    summary="Get order creation snapshot for an API log entry (by request_id)",
    dependencies=[Depends(require_admin)],
)
async def get_api_log_snapshot(
    request_id: str,
    audit_service: AuditService = Depends(get_service(AuditService)),
):
    return await audit_service.get_snapshot_by_request_id(request_id)


@router.get(
    "/{entity_type}/{entity_id}",
    response_model=List[AuditLogResponse],
    summary="Get audit logs for a specific entity",
    dependencies=[Depends(require_admin)],
)
async def get_entity_audit_logs(
    entity_type: str,
    entity_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    audit_service: AuditService = Depends(get_service(AuditService)),
):
    return await audit_service.get_logs_for_entity(
        entity_type=entity_type,
        entity_id=entity_id,
        skip=skip,
        limit=limit,
    )
