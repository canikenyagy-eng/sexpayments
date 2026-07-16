from uuid import UUID

from app.modules.base.schemas import BaseResponseSchema


class TraderClientOrderInfo(BaseResponseSchema):
    """Compact client block for the TRADER order modal — our internal ``public_id``
    plus all-time turnover and conversion, for the trader's OWN order only (gated
    by the merchant's ``unique_clients_enabled`` + order ownership; see
    ``ClientService.get_for_order_trader``). Deliberately restricted vs the admin
    block — no raw order counts, no ban controls."""

    public_id: UUID
    turnover_usdt: float = 0  # Σ amount_usdt of the client's SUCCESS orders
    conversion: float = 0  # successful / total, 0..1 (computed server-side)
