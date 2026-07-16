from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import Field

from app.modules.base.schemas import BaseResponseSchema, BaseSchema


class AuditLogBase(BaseSchema):
    user_id: Optional[str] = Field(None, description="ID of the user who performed the action")
    action: str = Field(..., description="Action performed")
    entity_type: str = Field(..., description="Type of entity affected")
    entity_id: str = Field(..., description="ID of the affected entity")
    old_values: Optional[Dict[str, Any]] = Field(None, description="Values before the action")
    new_values: Optional[Dict[str, Any]] = Field(None, description="Values after the action")
    request_id: Optional[str] = Field(None, description="ID of the request that triggered the action")


class AuditLogCreate(AuditLogBase):
    pass


class AuditLogResponse(BaseResponseSchema, AuditLogBase):
    id: int
    created_at: datetime


class MerchantApiLogResponse(BaseResponseSchema):
    request_id: Optional[str] = None
    merchant_id: Optional[int] = None
    order_id: Optional[int] = None
    url: str
    method: str
    request_headers: Optional[Dict[str, Any]] = None
    request_body: Optional[str] = None
    response_status: Optional[int] = None
    response_headers: Optional[Dict[str, Any]] = None
    response_body: Optional[str] = None
    response_time_ms: Optional[int] = None
    created_at: datetime


class OrderCreationSnapshotResponse(BaseResponseSchema):
    request_id: Optional[str] = None
    merchant_id: Optional[int] = None
    order_id: Optional[int] = None
    response_time_ms: Optional[int] = None
    request_data: Dict[str, Any]
    merchant_snapshot: Dict[str, Any]
    rate_snapshot: Optional[Dict[str, Any]] = None
    traders_snapshot: List[Dict[str, Any]]
    candidates: List[Dict[str, Any]]
    result: Dict[str, Any]
    created_at: datetime
