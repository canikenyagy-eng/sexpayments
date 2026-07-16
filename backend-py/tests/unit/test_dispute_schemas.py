"""Role-scoped dispute response schemas — the merchant and trader views must
NOT leak internal ids, counterparty identity, or internal storage paths. Both
expose ``evidence_count`` (how many files are attached) instead of the raw
``evidence_files`` receipt paths.
"""
import types
import uuid as uuidlib
from datetime import datetime

from app.common.enums.disputes import DisputeReason, DisputeStatus
from app.common.enums.users import UserRole


def _fake_dispute(**over):
    base = dict(
        id=7, uuid=uuidlib.uuid4(), order_id=11, merchant_id=3,
        reason=DisputeReason.NO_PAYMENT, status=DisputeStatus.OPEN, substatus=None,
        initiator_type=UserRole.MERCHANT, initiator_id=3,
        assigned_user_type=UserRole.TRADER, assigned_user_id=20,
        resolved_by_type=None, resolved_by_id=None,
        resolution_text=None, resolved_at=None,
        created_at=datetime(2026, 6, 13),
        evidence_files=["uploads/receipts/a.png", "uploads/receipts/b.pdf"],
        order_uuid="order-uuid", order_external_id="ext-1",
        order_amount=1000.0, order_payment_method="sbp",
        trader_login="trader_bob", merchant_login="merch_acme",
    )
    base.update(over)
    return types.SimpleNamespace(**base)


def test_merchant_response_hides_internal_and_counts_evidence():
    from app.modules.disputes.schemas.merchant import DisputeMerchantResponse

    out = DisputeMerchantResponse.model_validate(_fake_dispute()).model_dump(mode="json")

    assert out["evidence_count"] == 2
    assert "evidence_files" not in out
    for leaked in (
        "id", "order_id", "merchant_id", "initiator_id",
        "assigned_user_id", "assigned_user_type", "resolved_by_id",
        "resolved_by_type", "trader_login",
    ):
        assert leaked not in out, f"{leaked} leaked to merchant"

    # Merchant-relevant fields kept.
    assert out["reason"] == "no_payment"
    assert out["status"] == "open"
    assert out["order_external_id"] == "ext-1"   # merchant's OWN internalId — ok


def test_merchant_response_evidence_count_zero_when_empty():
    from app.modules.disputes.schemas.merchant import DisputeMerchantResponse

    out = DisputeMerchantResponse.model_validate(
        _fake_dispute(evidence_files=None)
    ).model_dump(mode="json")
    assert out["evidence_count"] == 0


def test_trader_response_hides_merchant_identity_and_paths():
    from app.modules.disputes.schemas.trader import DisputeTraderResponse

    out = DisputeTraderResponse.model_validate(_fake_dispute()).model_dump(mode="json")

    assert out["evidence_count"] == 2
    assert "evidence_files" not in out
    for leaked in (
        "merchant_id", "merchant_login", "order_external_id",
        "assigned_user_id", "initiator_id",
    ):
        assert leaked not in out, f"{leaked} leaked to trader"

    assert out["trader_login"] == "trader_bob"   # trader's OWN login — ok
