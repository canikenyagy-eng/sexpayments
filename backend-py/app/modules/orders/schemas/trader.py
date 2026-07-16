"""Trader-facing order schemas.

The field surface the trader UI renders in DashboardView /
ActiveOrdersView / DisputesView. ``admin.OrderResponse`` is the wider
shape used by admin views — it additionally carries merchant identity,
platform fees, profit and routing metadata.

Endpoints under ``require_trader`` declare
``response_model=OrderTraderResponse``.
"""
from datetime import datetime
from typing import Any, List, Optional
from uuid import UUID

from pydantic import model_validator

from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.receipt_moderations import is_receipt_visible_to_trader
from app.modules.base.schemas import BaseResponseSchema


class RequisiteTraderInfo(BaseResponseSchema):
    """Requisite block shown on the trader's order card.

    Mirrors the fields the trader templates actually read:
      * id / nickname — for "my requisite" linking in UI
      * bank_name / account_number / account_holder — what trader needs
        to confirm a payment
      * payment_method / currency — to render the right badges
      * logo_url — bank logo on the card
    """

    id: Optional[int] = None
    nickname: Optional[str] = None
    bank_name: str
    account_number: str
    account_holder: str
    payment_method: PaymentMethod
    currency: Currency
    logo_url: Optional[str] = None


class OrderTraderResponse(BaseResponseSchema):
    """Order shape returned to the trader UI.

    Carries what the trader's order views render: amounts, exchange
    rate, the trader's earning on the order (``trader_fee_usdt``),
    status, timestamps and the assigned requisite block.

    ``admin.OrderResponse`` is the wider shape — it additionally carries
    merchant identity, platform fees, profit, the merchant-side order id
    and routing metadata.
    """

    id: int
    uuid: UUID
    direction: PaymentDirection

    amount: float
    currency: Currency
    payment_method: PaymentMethod

    amount_usdt: Optional[float]
    exchange_rate: Optional[float]
    trader_fee_usdt: Optional[float]

    status: OrderStatus

    receipt_file: Optional[str]
    receipt_uploaded_at: Optional[datetime]

    created_at: datetime
    updated_at: datetime
    date_end: Optional[datetime]
    confirmed_at: Optional[datetime]
    rejected_at: Optional[datetime]

    rejection_reason: Optional[str]

    requisite: Optional[RequisiteTraderInfo] = None

    @model_validator(mode="before")
    @classmethod
    def _project_for_trader(cls, data: Any) -> Any:
        """Trader-side view projection: until premoderation is bypassed
        (``ModerationStatus.NONE``) or an admin clicks «Принять»
        (``APPROVED``), present the order to the trader as if no
        receipt had been uploaded at all — status rolls back to
        ``pending``, all receipt-related fields are nulled.

        Server-side state on the ``Order`` row is unchanged; this is
        purely the trader's coherent projection. Admin and merchant
        responses see the underlying values via their own schemas.
        """
        if not hasattr(data, "moderation_status"):
            return data
        if is_receipt_visible_to_trader(data.moderation_status):
            return data
        d = data.__dict__
        d["receipt_file"] = None
        d["receipt_uploaded_at"] = None
        d["confirmed_at"] = None
        # Roll the trader-visible status back from ``receipt_uploaded`` to
        # ``pending`` so it's consistent with the absent file. Other
        # statuses (``success`` after admin force-confirm, ``canceled``,
        # ``disputed``, etc.) are left alone — they're already correct.
        if data.status == OrderStatus.RECEIPT_UPLOADED:
            d["status"] = OrderStatus.PENDING
        return data

    @model_validator(mode="before")
    @classmethod
    def _populate_requisite(cls, data: Any) -> Any:
        """Build the requisite block from the ORM Order.

        Populates ``requisite`` with ``RequisiteTraderInfo`` — bank name,
        account details and the bank logo. The bank name prefers the
        payment option's name and falls back to the requisite's own.
        """
        if not hasattr(data, "requisite_id"):
            return data
        req = getattr(data, "requisite", None)
        if req is None:
            return data
        option = getattr(data, "payment_option", None)
        bank_name = (option.name if option else None) or req.bank_name
        data.__dict__["requisite"] = RequisiteTraderInfo(
            id=getattr(req, "id", None),
            nickname=getattr(req, "nickname", None),
            bank_name=bank_name,
            account_number=req.account_number,
            account_holder=req.account_holder,
            payment_method=req.payment_method,
            currency=req.currency,
            logo_url=option.logo_url if option else None,
        )
        return data


class PaginatedTraderOrderResponse(BaseResponseSchema):
    """Paginated envelope for the trader orders list — ``items`` + ``total`` so
    the UI can render exact page counts (mirrors the admin/merchant lists)."""
    items: List[OrderTraderResponse]
    total: int
