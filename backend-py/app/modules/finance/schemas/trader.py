"""Trader-facing finance schemas.

``WithdrawalRequestResponse`` in admin.py carries ``merchant_id`` and
``merchant_name`` so the admin withdrawal table can render the
counterparty column. Traders never need (and shouldn't see) which
merchant a withdrawal flowed through — same for ``processed_by_id``,
which exposes the admin operator.

Trader UI surface (per the FinancesView.vue audit):
  * Withdrawals table: id, amount, currency, fee_amount,
    destination_address, status, created_at
  * Balances list: id, type, currency, amount (plus optional user_id for
    self-disambiguation in teamlead mode)
"""
from datetime import datetime
from decimal import Decimal
from typing import Optional
from uuid import UUID

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency, WithdrawalStatus
from app.common.enums.users import UserRole
from app.modules.base.schemas import BaseResponseSchema, BaseSchema


class WithdrawalRequestTraderResponse(BaseResponseSchema):
    """Withdrawal shape returned to ``GET /finances/my-withdrawals``.

    Fields **intentionally NOT exposed** (kept in admin.WithdrawalRequestResponse):
      * merchant_id / merchant_name — merchants don't relate to trader
        withdrawals, but the shared schema leaked these (they belong to
        a separate merchant withdrawal flow)
      * processed_by_id — which admin operator approved/rejected
      * user_role / user_id / user_login — redundant (the trader is
        always the request author; their own login is in the auth
        context already)
    """

    id: int
    uuid: UUID
    amount: Decimal
    currency: Currency
    destination_address: str
    fee_amount: Decimal
    status: WithdrawalStatus
    created_at: datetime
    updated_at: datetime
    processed_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None


class BalanceTraderResponse(BaseSchema):
    """Balance row returned to ``GET /finances/my-balances``.

    Trader balances are owned by a user (the trader themselves) — the
    ``merchant_id`` field on the admin output is always None for trader
    rows, and ``is_system`` is always False. We drop both to remove the
    "what's a merchant column doing here" confusion from DevTools.
    """

    id: int
    type: BalanceType
    currency: Currency
    amount: float


class TraderFinanceStatsOrder(BaseSchema):
    order_id: int
    order_date: datetime
    amount_usdt: float
    profit_usdt: float


class TraderFinanceStatsResponse(BaseSchema):
    processed_usdt: float
    profit_usdt: float
    orders: list[TraderFinanceStatsOrder]
