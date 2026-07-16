from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import Field, computed_field

from app.modules.base.schemas import BaseResponseSchema, BaseSchema


class AdminClientResponse(BaseResponseSchema):
    """Admin view of one merchant client (a unique merchant+clientID pair)."""

    id: int
    merchant_id: int
    merchant_name: Optional[str] = None  # enriched from the merchant
    client_user_id: str

    is_blocked: bool
    block_reason: Optional[str] = None
    blocked_by_admin_id: Optional[int] = None
    blocked_at: Optional[datetime] = None

    first_seen_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None

    # Rollup stats (materialised off the hot path). ``conversion`` is derived.
    total_orders: int = 0
    successful_orders: int = 0
    turnover_usdt: float = 0  # Σ amount_usdt of SUCCESS orders
    blocked_attempts: int = 0  # withheld requisites while the client was blocked

    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def conversion(self) -> float:
        """Successful / total as a 0..1 fraction (0 when the client has no orders)."""
        return round(self.successful_orders / self.total_orders, 4) if self.total_orders else 0.0


class AdminClientOrderInfo(BaseResponseSchema):
    """Compact client block for the admin order modal — our internal ``public_id``
    plus all-time deal count and conversion. Served ONLY when the order's merchant
    has ``unique_clients_enabled`` (gated in ``ClientService.get_for_order``)."""

    public_id: UUID
    total_orders: int = 0
    successful_orders: int = 0
    turnover_usdt: float = 0  # Σ amount_usdt of the client's SUCCESS orders

    @computed_field
    @property
    def conversion(self) -> float:
        """Successful / total as a 0..1 fraction (0 when the client has no orders)."""
        return round(self.successful_orders / self.total_orders, 4) if self.total_orders else 0.0


class AdminClientBlockRequest(BaseSchema):
    """Block a client by the natural key — works from the Clients page AND the
    order modal (upserts the client if it isn't materialised yet)."""

    merchant_id: int = Field(..., description="Merchant the client belongs to")
    client_user_id: str = Field(..., min_length=1, max_length=255, description="Merchant's clientID / userId")
    reason: Optional[str] = Field(None, max_length=500, description="Why the client is blocked")


class AdminClientUnblockRequest(BaseSchema):
    merchant_id: int = Field(..., description="Merchant the client belongs to")
    client_user_id: str = Field(..., min_length=1, max_length=255, description="Merchant's clientID / userId")
