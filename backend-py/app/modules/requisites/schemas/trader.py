"""Trader-facing requisite schema.

Mostly the same fields as admin.RequisiteResponse — the trader actually
owns their requisites, so most data is theirs to see. The only leak is
``trader_id`` (redundant: it's always the current user) and the
``trader`` proxy / ``trader_login`` computed field (your own login is
already in auth context).
"""
from datetime import datetime
from typing import Optional

from pydantic import Field

from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.requisites import RequisiteStatus
from app.modules.base.schemas import BaseResponseSchema, BaseSchema
from app.modules.payments.schemas import PaymentOptionResponse
from app.modules.requisites.schemas.admin import RequisiteLimitResponse, RequisiteLimitUpdate


class RequisiteTraderUpdate(BaseSchema):
    """Trader-facing requisite update (``PATCH /requisites/me/{id}``).

    Deliberately omits ``status`` / ``is_active`` / ``is_archived`` — those are
    admin-only. A trader changes status only through the guarded
    ``/me/{id}/enable`` and ``/me/{id}/disable`` routes, which enforce the
    ``BLOCKED`` guard. Mirrors the editable fields of the admin
    ``RequisiteUpdate`` minus the admin flags.
    """

    nickname: Optional[str] = Field(None, max_length=100)
    payment_option_id: Optional[int] = Field(None, description="Изменить банк (опцию оплаты)")
    account_number: Optional[str] = Field(None, min_length=5, max_length=100)
    account_holder: Optional[str] = Field(None, min_length=2, max_length=255)
    payment_method: Optional[PaymentMethod] = None
    limits: Optional[RequisiteLimitUpdate] = None
    trader_priority: Optional[int] = Field(None, ge=1, le=3, description="Приоритет реквизита (1–3)")


class RequisiteTraderResponse(BaseResponseSchema):
    """Requisite shape returned to ``GET /requisites/me*`` endpoints.
    """

    id: int
    nickname: Optional[str] = None
    bank_name: str
    account_number: str
    account_holder: str
    payment_method: PaymentMethod
    currency: Currency
    payment_option_id: Optional[int] = None
    payment_option: Optional[PaymentOptionResponse] = None
    status: RequisiteStatus
    is_active: bool
    is_archived: bool
    trader_priority: int
    last_used_at: Optional[datetime]
    limits: Optional[RequisiteLimitResponse]
