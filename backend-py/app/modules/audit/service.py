from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.audit import repository as ch_logs
from app.modules.audit.models import AuditLog
from app.modules.audit.repository import AuditLogRepository
from app.modules.audit.schemas import AuditLogCreate
from app.modules.base.service import BaseService


def _sanitize_for_json(obj: Any) -> Any:
    """Recursively convert non-JSON-serializable types to primitives."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, date):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _sanitize_for_json(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_for_json(v) for v in obj]
    return str(obj)


class AuditService(BaseService):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        self.repository = AuditLogRepository(session)

    async def log_action(
        self,
        action: str,
        entity_type: str,
        entity_id: str,
        user_id: Optional[str] = None,
        old_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ) -> AuditLog:
        if not request_id:
            from app.core.middleware.request_id import request_id_context_var
            request_id = request_id_context_var.get()

        data = AuditLogCreate(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            user_id=user_id,
            old_values=_sanitize_for_json(old_values) if old_values else old_values,
            new_values=_sanitize_for_json(new_values) if new_values else new_values,
            request_id=request_id,
        )
        return await self.repository.create(data.model_dump())

    async def list_api_logs(
        self,
        skip: int = 0,
        limit: int = 50,
        merchant_id: Optional[int] = None,
        method: Optional[str] = None,
        endpoint_group: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_order: Optional[str] = "desc",
    ) -> List[Dict[str, Any]]:
        return await ch_logs.list_merchant_api_logs(
            skip=skip, limit=limit, merchant_id=merchant_id, method=method,
            endpoint_group=endpoint_group, sort_by=sort_by, sort_order=sort_order,
        )

    async def get_logs_for_entity(
        self, entity_type: str, entity_id: str, skip: int = 0, limit: int = 100
    ) -> List[AuditLog]:
        """
        Retrieve audit logs for a specific entity.
        """
        # we can add a specific method to the repository later if needed
        from sqlalchemy import select
        
        result = await self.session.execute(
            select(AuditLog)
            .where(AuditLog.entity_type == entity_type)
            .where(AuditLog.entity_id == entity_id)
            .order_by(AuditLog.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_snapshot_by_request_id(self, request_id: str) -> Optional[Dict[str, Any]]:
        return await ch_logs.get_snapshot_by_request_id(request_id)
