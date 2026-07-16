"""Trader-facing dispute schemas.

Pairs with ``app.modules.disputes.schemas.admin.DisputeResponse``.
Strips counterparty identity (merchant_id / merchant_login) and
internal assignment/resolution metadata that the trader UI never
reads — but keeps everything that's actually rendered in
``DisputesView.vue`` (the list and the detail modal).

Surface the trader UI reads (per the frontend audit):
  * id, uuid, status, reason, created_at
  * order_amount, order_payment_method, order_uuid
  * resolution_text, resolved_at
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import Field, computed_field

from app.common.enums.disputes import DisputeReason, DisputeStatus, DisputeSubstatus
from app.modules.base.schemas import BaseResponseSchema


class DisputeTraderResponse(BaseResponseSchema):
    """Dispute shape returned to ``GET /disputes/my`` and ``/disputes/my/{uuid}``.

    Fields **intentionally NOT exposed** (kept in admin.DisputeResponse):
      * merchant_id / merchant_login — counterparty identity leak
      * initiator_type / initiator_id — operational metadata
      * assigned_user_type / assigned_user_id — internal routing
      * resolved_by_type / resolved_by_id — who closed it
      * order_id — internal numeric id (order_uuid is the public handle)
      * order_external_id — the MERCHANT's internalId; counterparty identifier
        the trader must not see (order_uuid is enough to recognise the order)
    """

    id: int
    uuid: UUID

    reason: DisputeReason

    status: DisputeStatus
    substatus: Optional[DisputeSubstatus] = None

    resolution_text: Optional[str]
    resolved_at: Optional[datetime]

    created_at: Optional[datetime] = None

    # Order snapshot — what the trader actually needs to recognise which
    # of their orders is in dispute. ``trader_login`` is the trader's own
    # login (safe to expose — it's their own).
    trader_login: Optional[str] = None
    order_uuid: Optional[str] = None
    order_amount: Optional[float] = None
    order_payment_method: Optional[str] = None

    # Read from the ORM only to derive the count — the raw receipt paths are
    # internal and never serialised to the trader.
    evidence_files: Optional[List[str]] = Field(default=None, exclude=True)

    @computed_field  # type: ignore[misc]
    @property
    def evidence_count(self) -> int:
        return len(self.evidence_files or [])
