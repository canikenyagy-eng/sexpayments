from typing import Any, Dict, Optional
from sqlalchemy.ext.asyncio import AsyncSession


class BaseService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def audit_log(
        self,
        action: str,
        entity_type: str,
        entity_id: Any,
        user_id: Optional[Any] = None,
        old_values: Optional[Dict[str, Any]] = None,
        new_values: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Helper method to log audit actions easily from any service."""
        from app.modules.audit.service import AuditService
        audit_service = AuditService(self.session)
        await audit_service.log_action(
            action=action,
            entity_type=entity_type,
            entity_id=str(entity_id),
            user_id=str(user_id) if user_id else None,
            old_values=old_values,
            new_values=new_values,
        )
