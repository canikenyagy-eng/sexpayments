from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import Field, model_validator

from app.common.enums.finances import Currency
from app.common.enums.orders import OrderSource, OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.modules.audit.schemas import MerchantApiLogResponse
from app.modules.base.schemas import BaseResponseSchema, BaseSchema
from app.modules.disputes.schemas import DisputeResponse


class RequisiteInfo(BaseResponseSchema):
    """Payment requisite details included when order has an assigned requisite."""
    id: Optional[int] = None
    nickname: Optional[str] = None
    bank_name: str
    account_number: str
    account_holder: str
    payment_method: PaymentMethod
    currency: Currency
    payment_option_id: Optional[int] = None
    payment_option_code: Optional[str] = None
    payment_option_name: Optional[str] = None
    logo_url: Optional[str] = None


class MerchantRequisiteInfo(BaseResponseSchema):
    """Payment requisite details for merchant responses"""
    bank_name: str
    account_number: str
    account_holder: str
    payment_method: PaymentMethod
    currency: Currency
    payment_option_code: Optional[str] = None
    payment_option_name: Optional[str] = None


class FailOrderRequest(BaseSchema):
    reason: str


class AdminOrderUpdate(BaseSchema):
    status: Optional[OrderStatus] = None
    amount: Optional[float] = Field(None, gt=0, description="New amount in fiat (RUB)")
    reason: Optional[str] = Field(None, max_length=500, description="Reason for the change")


class OrderBase(BaseSchema):
    amount: float = Field(..., gt=0, description="Amount in fiat")
    currency: Currency
    payment_method: PaymentMethod = Field(..., description="e.g., card, sbp, sim")
    webhook_url: Optional[str] = None


class OrderCreate(OrderBase):
    external_id: str = Field(..., description="Merchant's internal transaction ID")
    direction: PaymentDirection = Field(default=PaymentDirection.PAYIN)
    fingerprint: Optional[str] = None


class OrderResponse(BaseResponseSchema, OrderBase):
    id: int
    uuid: UUID
    external_id: str
    merchant_id: int
    merchant_name: Optional[str] = None
    direction: PaymentDirection
    source: OrderSource

    amount_usdt: Optional[float]
    exchange_rate: Optional[float]
    fee_usdt: Optional[float]
    profit_usdt: Optional[float]

    status: OrderStatus

    receipt_file: Optional[str]
    receipt_uploaded_at: Optional[datetime]

    created_at: datetime
    updated_at: datetime
    date_end: Optional[datetime]
    confirmed_at: Optional[datetime]
    rejected_at: Optional[datetime]

    rejection_reason: Optional[str]

    # Merchant-cabinet view gets the restricted requisite shape (bank details
    # only). Admin overrides this back to the full RequisiteInfo below.
    requisite: Optional[MerchantRequisiteInfo] = None

    @model_validator(mode="before")
    @classmethod
    def _populate_requisite(cls, data: Any) -> Any:
        if not hasattr(data, "requisite_id"):
            return data

        merchant = getattr(data, "merchant", None)
        if merchant is not None and not getattr(data, "merchant_name", None):
            data.__dict__["merchant_name"] = getattr(merchant, "name", None)

        req = getattr(data, "requisite", None)
        if req is None:
            return data
        option = getattr(data, "payment_option", None)
        option_id = getattr(data, "payment_option_id", None)
        bank_name = (option.name if option else None) or req.bank_name
        # NOTE: this always builds the FULL RequisiteInfo. The merchant-facing
        # base OrderResponse types `requisite` as the restricted
        # MerchantRequisiteInfo, so Pydantic coerces this DOWN (dropping
        # id/nickname/logo/payment_option_id) at serialization; AdminOrderResponse
        # overrides the field back to the full RequisiteInfo. The leak protection
        # therefore lives in the field annotation, not here.
        data.__dict__["requisite"] = RequisiteInfo(
            id=getattr(req, "id", None),
            nickname=getattr(req, "nickname", None),
            bank_name=bank_name,
            account_number=req.account_number,
            account_holder=req.account_holder,
            payment_method=req.payment_method,
            currency=req.currency,
            payment_option_id=option_id,
            payment_option_code=option.code if option else None,
            payment_option_name=option.name if option else None,
            logo_url=option.logo_url if option else None,
        )
        return data


class AdminOrderResponse(OrderResponse):
    """Admin-only order view. Holds fields that must NOT reach the merchant
    cabinet, which shares ``OrderResponse`` (see merchants.py ``/me/orders``,
    ``require_merchant``). ``provider_order_id`` would reveal that the deal was
    routed through a cascade provider + the provider's own order id.
    """
    # Counterparty/internal fields kept OUT of the merchant-facing OrderResponse:
    # trader_id / requisite_id (internal ids) and trader_fee_usdt (the trader's
    # per-order reward) are cross-principal data. requisite is restored to the
    # full RequisiteInfo (the restricted MerchantRequisiteInfo on the base hides
    # the requisite's id/nickname/logo from merchants).
    trader_id: Optional[int] = None
    requisite_id: Optional[int] = None
    trader_fee_usdt: Optional[float] = None
    requisite: Optional[RequisiteInfo] = None

    provider_order_id: Optional[str] = None  # set ⇒ deal routed via cascade
    client_user_id: Optional[str] = None  # merchant's clientID — shown in the order modal's participants
    teamlead_reward_usdt: Optional[float] = Field(None, description="Σ teamlead rewards paid for the order")
    platform_profit_usdt: Optional[float] = Field(None, description="Platform net margin = fee − trader_fee − teamlead_reward")
    financials: Optional[dict] = Field(None, description="Financial snapshot: per-teamlead breakdown + headline figures")


class OrderStatusHistoryResponse(BaseResponseSchema):
    id: int
    order_id: int
    old_status: Optional[OrderStatus]
    new_status: OrderStatus
    changed_by_user_id: Optional[int]
    reason: Optional[str]
    created_at: datetime


class MerchantPayinCreate(BaseSchema):
    amount: float = Field(..., gt=0, description="Amount in fiat")
    currency: Currency = Field(..., description="Currency code (e.g., RUB)")
    payment_method: PaymentMethod = Field(..., description="Payment method (e.g., card, sbp, sim)")
    
    notificationUrl: Optional[str] = Field(None, description="URL for webhook notifications")
    internalId: Optional[str] = Field(None, description="Order ID in merchant's system")
    userId: Optional[str] = Field(None, description="Client ID in merchant's system")
    payment_option: Optional[int] = Field(None, description="Specific bank/option ID")
    
    issue_requisite_async: bool = Field(
        False, 
        description="If true, the API returns immediately with CREATED status. If false (default), it waits for a requisite to be assigned before responding."
    )


class MerchantOrderResponse(BaseResponseSchema):
    id: str = Field(..., description="UUID of the order in PrimePay")
    internalId: Optional[str] = Field(None, description="Order ID in merchant's system")
    userId: Optional[str] = Field(None, description="Client ID in merchant's system")
    merchant_name: Optional[str] = Field(None, description="Merchant name")
    amount: float
    amount_usdt: Optional[float] = Field(None, description="Amount in USDT at the time of order creation")
    fee_usdt: Optional[float] = Field(None, description="Merchant commission in USDT")
    exchange_rate: Optional[float] = Field(None, description="Exchange rate used for this order")
    currency: Currency
    status: OrderStatus
    payment_url: str = Field(..., description="URL to redirect the client for payment")
    created_at: datetime
    expires_at: Optional[datetime] = Field(None, description="Order expiry deadline")
    requisite: Optional[MerchantRequisiteInfo] = Field(
        None,
        description="Assigned requisite details (only present when order is created synchronously and a requisite is found)",
    )


# --- Debug Schemas ---


class CallbackAttemptResponse(BaseResponseSchema):
    id: int
    order_id: int
    url: str
    request_headers: Optional[Dict[str, Any]]
    request_payload: Dict[str, Any]
    response_status: Optional[int]
    response_headers: Optional[Dict[str, Any]]
    response_body: Optional[str]
    is_successful: bool
    attempt_number: int
    created_at: datetime


class LedgerEntryResponse(BaseResponseSchema):
    id: int
    from_balance_id: Optional[int]
    to_balance_id: Optional[int]
    amount: float
    currency: Currency
    reference_type: str
    reference_id: str
    description: Optional[str]
    created_at: datetime


class TraderCandidateInfo(BaseResponseSchema):
    trader_id: Optional[int] = None
    user_id: Optional[int] = None
    username: Optional[str] = None
    requisite_id: Optional[int] = None
    is_selected: bool = False
    is_excluded: bool = False
    reason: Optional[str] = None
    status: Optional[str] = None
    is_payin_active: Optional[bool] = None
    is_payout_active: Optional[bool] = None
    method_config: Optional[Dict[str, Any]] = None


class OrderDebugResponse(BaseResponseSchema):
    order: AdminOrderResponse
    status_history: List[OrderStatusHistoryResponse]
    merchant_api_logs: List[MerchantApiLogResponse]
    callback_attempts: List[CallbackAttemptResponse]
    ledger_entries: List[LedgerEntryResponse]
    dispute: Optional[DisputeResponse]
    traders_candidates: List[TraderCandidateInfo] = []


class PaginatedAdminOrderResponse(BaseResponseSchema):
    """Paginated envelope for the admin orders list — ``items`` + ``total`` so the
    UI can render exact page counts (mirrors PaginatedOrderRequestsResponse)."""
    items: List[AdminOrderResponse]
    total: int


class PaginatedOrderResponse(BaseResponseSchema):
    """Paginated envelope for the merchant-cabinet orders list (generic
    OrderResponse items) — ``items`` + ``total``."""
    items: List[OrderResponse]
    total: int
