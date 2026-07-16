"""The merchant cabinet (`GET /api/v1/merchants/me/orders[/{uuid}]`) serialises
via `OrderResponse`. That base must NOT expose the counterparty trader's
per-order earnings (`trader_fee_usdt`) or internal `trader_id`/`requisite_id`,
nor the requisite's internal id/nickname/logo — those belong only to the
admin view (`AdminOrderResponse`). This pins the field split.
"""
from typing import Optional

from app.modules.orders.schemas.admin import (
    AdminOrderResponse,
    MerchantRequisiteInfo,
    OrderResponse,
    RequisiteInfo,
)


def test_merchant_facing_order_hides_trader_earnings_and_internal_ids():
    for leaked in ("trader_id", "requisite_id", "trader_fee_usdt"):
        assert leaked not in OrderResponse.model_fields, (
            f"{leaked} leaks to the merchant cabinet via OrderResponse"
        )
        assert leaked in AdminOrderResponse.model_fields, (
            f"{leaked} must remain visible to admin via AdminOrderResponse"
        )


def test_merchant_facing_requisite_is_restricted_shape():
    # Merchant cabinet gets the restricted requisite (bank details only, no
    # internal id / nickname / logo_url / payment_option_id); admin keeps full.
    assert OrderResponse.model_fields["requisite"].annotation == Optional[MerchantRequisiteInfo]
    assert AdminOrderResponse.model_fields["requisite"].annotation == Optional[RequisiteInfo]


def test_merchant_order_serialization_drops_requisite_internal_fields():
    """Runtime: serializing an order via OrderResponse (merchant cabinet) drops
    the requisite's internal id/nickname and never carries trader_id /
    trader_fee_usdt, even though the source object has them."""
    from datetime import datetime
    from types import SimpleNamespace
    from uuid import uuid4

    from app.common.enums.finances import Currency
    from app.common.enums.orders import OrderSource, OrderStatus
    from app.common.enums.payments import PaymentDirection, PaymentMethod

    req = SimpleNamespace(
        id=777, nickname="secret-nick", bank_name="Sber",
        account_number="40817810000000000001", account_holder="Ivan",
        payment_method=PaymentMethod.CARD, currency=Currency.RUB,
    )
    order = SimpleNamespace(
        id=1, uuid=uuid4(), external_id="ext-1", merchant_id=2, merchant=None,
        merchant_name="M", direction=PaymentDirection.PAYIN, source=OrderSource.API,
        amount=100.0, currency=Currency.RUB, payment_method=PaymentMethod.CARD,
        webhook_url=None, amount_usdt=1.0, exchange_rate=100.0, fee_usdt=0.1,
        trader_fee_usdt=5.0, trader_id=42, profit_usdt=0.05,
        status=OrderStatus.SUCCESS, receipt_file=None, receipt_uploaded_at=None,
        created_at=datetime(2026, 7, 1), updated_at=datetime(2026, 7, 1),
        date_end=None, confirmed_at=None, rejected_at=None, rejection_reason=None,
        requisite_id=777, requisite=req, payment_option=None, payment_option_id=None,
    )

    dumped = OrderResponse.model_validate(order).model_dump()
    assert "trader_id" not in dumped and "trader_fee_usdt" not in dumped and "requisite_id" not in dumped
    assert dumped["requisite"]["bank_name"] == "Sber"
    assert "id" not in dumped["requisite"] and "nickname" not in dumped["requisite"]
