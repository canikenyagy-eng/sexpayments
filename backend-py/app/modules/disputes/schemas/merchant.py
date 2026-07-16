"""Merchant-facing dispute schemas (restricted).

Returned to both the merchant HMAC API (``/api/merchant/v1/disputes``) and the
merchant cabinet (``/api/v1/merchants/me/disputes``). Self-contained — no
cross-role imports.

**Never exposes** internal ids (``id`` / ``order_id`` / ``merchant_id`` /
``initiator_id``), trader/assignment routing (``assigned_user_*`` /
``resolved_by_*`` / ``trader_login``), or the internal ``evidence_files`` storage
paths. Evidence is summarised as ``evidence_count``.
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import Field, computed_field

from app.common.enums.disputes import DisputeReason, DisputeStatus, DisputeSubstatus
from app.common.enums.users import UserRole
from app.modules.base.schemas import BaseResponseSchema


class DisputeMerchantResponse(BaseResponseSchema):
    uuid: UUID

    reason: DisputeReason
    status: DisputeStatus
    substatus: Optional[DisputeSubstatus] = None

    resolution_text: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    # Who opened it — "merchant" for a merchant-raised dispute, "system" for a
    # premoderation one. A role string, not an identity.
    initiator_type: Optional[UserRole] = None

    # Order snapshot — the merchant's own data (``order_external_id`` is their
    # internalId), enough to recognise which order is in dispute.
    order_uuid: Optional[str] = None
    order_external_id: Optional[str] = None
    order_amount: Optional[float] = None
    order_payment_method: Optional[str] = None

    # Read from the ORM to derive the count, but never serialised — the raw
    # receipt paths are internal.
    evidence_files: Optional[List[str]] = Field(default=None, exclude=True)

    @computed_field  # type: ignore[misc]
    @property
    def evidence_count(self) -> int:
        return len(self.evidence_files or [])
