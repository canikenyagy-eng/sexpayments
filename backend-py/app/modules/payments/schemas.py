from typing import List, Optional
from pydantic import Field

from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.modules.base.schemas import BaseResponseSchema


class PaymentOptionResponse(BaseResponseSchema):
    id: int
    code: str
    name: str
    logo_url: Optional[str]
    supported_methods: List[str]
    currency: Currency
    is_active: bool


class MerchantPaymentOptionResponse(BaseResponseSchema):
    """Merchant-facing payment option — restricted variant of
    ``PaymentOptionResponse`` that omits ``logo_url`` (an internal asset path
    that is not part of the merchant API contract)."""

    id: int
    code: str
    name: str
    supported_methods: List[str]
    currency: Currency
    is_active: bool
