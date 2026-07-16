"""Trader-facing receipt-check schema.

The trader UI (ActiveOrdersView) only reads ``status`` + ``is_clean`` to
render a chip on each order row. The full admin shape leaks the internal
provider topology (``provider_id``), the per-trader pricing
(``price_usdt`` / ``charged`` / ``refunded``) and provider-error
diagnostics — none of which the trader needs.
"""
from datetime import datetime
from typing import List, Optional

from app.common.enums.receipt_checks import ReceiptCheckStatus, ReceiptCheckTrigger
from app.modules.base.schemas import BaseResponseSchema
from app.modules.receipt_checks.schemas.admin import ReceiptCheckVerdictItem


class TraderReceiptProviderResponse(BaseResponseSchema):
    """Slim provider projection for the trader-facing "pick a provider" list.

    Exposes **only** what the trader needs to choose — id, display name and
    per-check price. Deliberately omits every admin/internal field
    (``code`` / ``adapter_type`` / ``base_url`` / ``api_key`` / ``settings`` /
    ``is_active``): the endpoint already returns active providers only, and the
    receipt-check vendor topology must not leak to traders.

    ``price_usdt`` is a ``float`` (display-only) so it serializes as a JSON
    number rather than a string — mirroring the doliv trader schema. The real
    money math still uses the ORM ``Decimal`` server-side.
    """

    id: int
    name: str
    price_usdt: float

    @classmethod
    def from_orm_provider(cls, provider) -> "TraderReceiptProviderResponse":
        return cls(
            id=provider.id,
            name=provider.name,
            price_usdt=float(provider.price_usdt or 0),
        )


class ReceiptCheckTraderResponse(BaseResponseSchema):
    """Slim receipt-check projection for trader-side endpoints.

    Fields **intentionally NOT exposed** (kept in admin.ReceiptCheckResponse):
      * provider_id — exposes which receipt-check vendor we use
      * trader_user_id — redundant (it's the caller's own id)
      * price_usdt / charged / refunded — internal pricing
      * provider_check_id / error_code / error_message — vendor diagnostics
    """

    id: int
    order_id: int
    trigger: ReceiptCheckTrigger
    status: ReceiptCheckStatus
    is_clean: Optional[bool] = None
    verdict: Optional[List[ReceiptCheckVerdictItem]] = None
    parsed_data: Optional[dict] = None
    created_at: datetime
    finished_at: Optional[datetime] = None

    @classmethod
    def from_orm_check(cls, check) -> "ReceiptCheckTraderResponse":
        """Project the ORM row into the trader-safe shape."""
        return cls(
            id=check.id,
            order_id=check.order_id,
            trigger=check.trigger,
            status=check.status,
            is_clean=check.is_clean,
            verdict=check.verdict,
            parsed_data=check.parsed_data,
            created_at=check.created_at,
            finished_at=check.finished_at,
        )
