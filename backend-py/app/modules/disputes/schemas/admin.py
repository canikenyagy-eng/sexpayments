from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import Field

from app.common.enums.disputes import DisputeReason, DisputeStatus, DisputeSubstatus
from app.common.enums.users import UserRole
from app.modules.base.schemas import BaseResponseSchema, BaseSchema


class DisputeBase(BaseSchema):
    reason: DisputeReason
    evidence_files: Optional[List[str]] = Field(default_factory=list)


class DisputeCreate(DisputeBase):
    pass


class DisputeResolutionRequest(BaseSchema):
    resolution_text: str


class DisputeResponse(BaseResponseSchema, DisputeBase):
    id: int
    uuid: UUID
    order_id: int
    merchant_id: int

    initiator_type: UserRole
    initiator_id: Optional[int]

    status: DisputeStatus
    substatus: Optional[DisputeSubstatus] = None

    assigned_user_type: Optional[UserRole]
    assigned_user_id: Optional[int]

    resolution_text: Optional[str]
    resolved_by_type: Optional[UserRole]
    resolved_by_id: Optional[int]
    resolved_at: Optional[datetime]

    created_at: Optional[datetime] = None

    trader_login: Optional[str] = None
    merchant_login: Optional[str] = None
    order_uuid: Optional[str] = None
    order_external_id: Optional[str] = None
    order_amount: Optional[float] = None
    order_payment_method: Optional[str] = None
