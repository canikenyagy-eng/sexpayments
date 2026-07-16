"""Unit tests for the Payscrow cascade adapter.

Payscrow has a few characteristics worth pinning down:
  * Single X-API-Key header (no signing, no timestamp).
  * Webhook auth = same X-API-Key replayed back (token-compare via
    base's WEBHOOK_TOKEN_HEADER knob — no override on Payscrow side).
  * Cancel / receipt-forward are no-ops by design.
  * Dispute is a 2-step flow: fetch order to learn amount → multipart
    POST with files[] (multi-file via base.upload_files).
  * Webhook body wraps the order in ``payload`` (envelope.payload.id /
    .status / .amount).
"""
from __future__ import annotations

import json
import os
import tempfile
from decimal import Decimal
from typing import Any, Dict
from unittest.mock import MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost:5432/unused")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-tests-must-be-long!")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")

import pytest

from app.common.enums.cascading import ProviderStatus
from app.common.enums.payments import PaymentMethod
from app.core.security import encrypt_api_secret
from app.modules.cascading.integrations.base import (
    CallbackVerificationError,
    ProviderRefusal,
    ProviderRequisiteResponse,
)
from app.modules.cascading.integrations.payscrow import PayscrowAdapter
from tests.unit.cascade_adapter_testkit import (
    FakeRequest as _FakeRequest,
    FakeResponse as _FakeResponse,
    make_httpx_patch as _patch_httpx,
)


API_KEY = "be2f7293-3921-4300-85be-7c83adbb2c15"
BASE_URL = "https://api.payscrow-cascade.io"


def _provider(
    settings: Dict[str, Any] | None = None,
    *,
    encrypted: bool = True,
):
    p = MagicMock()
    p.id = 44
    p.code = "payscrow"
    p.adapter_type = "payscrow"
    p.base_url = BASE_URL
    p.api_secret_encrypted = encrypt_api_secret(API_KEY) if encrypted else None
    p.api_key_encrypted = None
    p.webhook_secret_encrypted = None
    base_settings: Dict[str, Any] = {
        "default_currency": "RUB",
        "unique_amount": True,
    }
    base_settings.update(settings or {})
    p.settings = base_settings
    p.request_timeout_ms = 5000
    p.cancel_timeout_ms = 2000
    return p


# ─── Auth: only X-API-Key ────────────────────────────────────


def test_sign_request_only_emits_x_api_key():
    adapter = PayscrowAdapter()
    headers = adapter.sign_request(
        token=API_KEY, method="POST", path="/api/v1/order/", body={"a": 1}
    )
    assert headers == {"X-API-Key": API_KEY}


def test_sign_request_idempotency_ignored():
    adapter = PayscrowAdapter()
    headers = adapter.sign_request(
        token=API_KEY, method="POST", path="/x", body=None, idempotency_key="abc"
    )
    assert "X-Idempotency-Key" not in headers


# ─── supports ────────────────────────────────────────────────


def test_supports_default_methods():
    adapter = PayscrowAdapter()
    for method in (PaymentMethod.SBP, PaymentMethod.CARD):
        assert adapter.supports(
            provider=_provider(), method=method, payment_option_code=None
        ), method


def test_supports_excludes_sim():
    adapter = PayscrowAdapter()
    # SIM is not in SUPPORTED_METHODS for Payscrow.
    assert not adapter.supports(
        provider=_provider(), method=PaymentMethod.SIM, payment_option_code=None
    )


# ─── issue_requisite ────────────────────────────────────────


_ORDER_RESPONSE = {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "client_order_id": "primepay-1",
    "currency": "RUB",
    "method_id": "8fe3669a-a448-4053-bc4b-43bb51cb3e9d",
    "method_name": "Сбербанк",
    "initial_method_id": "8fe3669a-a448-4053-bc4b-43bb51cb3e9d",
    "method_type": "BankCard",
    "order_side": "Buy",
    "amount": "1000.00",
    "fee": "15.0",
    "status": "Unpaid",
    "holder_name": "Иванов Иван Иванович",
    "holder_account": "4627100101654724",
    "customer_name": None,
    "customer_account": None,
    "payment_link": None,
    "created_at": "2024-07-04T15:40:20.569613Z",
    "updated_at": "2024-07-04T15:40:20.569613Z",
    "expires_at": "2024-07-04T16:10:20.569613Z",
}


@pytest.mark.asyncio
async def test_issue_requisite_happy_path():
    adapter = PayscrowAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "POST"
        assert req.url.endswith("/api/v1/order/")
        assert req.headers["X-API-Key"] == API_KEY
        body = json.loads(req.read())
        assert body["client_order_id"] == "primepay-1"
        assert body["order_side"] == "Buy"
        assert body["method_type"] == "BankCard"
        assert body["amount"] == "1000"  # format_amount on Decimal("1000")
        assert body["unique_amount"] is True
        assert "nspk_code" not in body  # nothing in bank map yet
        return _FakeResponse(201, _ORDER_RESPONSE)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("1000"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": None,
                "merchant_request_id": "primepay-1",
                "client_user_id": "user-42",
            },
            idempotency_key="idem-1",
            timeout_ms=5000,
        )

    assert isinstance(result, ProviderRequisiteResponse)
    assert result.external_order_id == "3fa85f64-5717-4562-b3fc-2c963f66afa6"
    assert result.bank_name == "Сбербанк"
    assert result.account_number == "4627100101654724"
    assert result.account_holder == "Иванов Иван Иванович"
    assert result.payment_method == PaymentMethod.CARD
    assert result.amount_fiat == Decimal("1000.00")
    # supports_provider_rate = False, so provider_rate should be None.
    assert result.provider_rate is None


@pytest.mark.asyncio
async def test_issue_requisite_sbp_uses_sbp_method_type():
    adapter = PayscrowAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        body = json.loads(req.read())
        assert body["method_type"] == "SBP"
        return _FakeResponse(201, {**_ORDER_RESPONSE, "method_type": "SBP"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("500"),
                "payment_method": PaymentMethod.SBP,
                "payment_option_code": None,
            },
            idempotency_key="idem-sbp",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRequisiteResponse)
    assert result.payment_method == PaymentMethod.SBP


@pytest.mark.asyncio
async def test_issue_requisite_passes_nspk_code_when_mapped():
    adapter = PayscrowAdapter()
    p = _provider({"nspk_code_map": {"sber": "bank100000000001"}})

    def responder(req: _FakeRequest):
        body = json.loads(req.read())
        assert body["nspk_code"] == "bank100000000001"
        return _FakeResponse(201, _ORDER_RESPONSE)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("1000"),
                "payment_method": PaymentMethod.SBP,
                "payment_option_code": "sber",
            },
            idempotency_key="x",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRequisiteResponse)


@pytest.mark.asyncio
async def test_issue_requisite_500_returns_no_capacity():
    """Payscrow uses 500 for `no available traders`, treat as soft refusal."""
    adapter = PayscrowAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(
            500,
            {"message": "No available traders that match order requirements."},
        )

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("100"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": None,
            },
            idempotency_key="x",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "no_capacity"


@pytest.mark.asyncio
async def test_issue_requisite_402_returns_insufficient_balance():
    adapter = PayscrowAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(402, {"message": "Insufficient balance"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("10000"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": None,
            },
            idempotency_key="x",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "insufficient_balance"


@pytest.mark.asyncio
async def test_issue_requisite_400_falls_through_to_refusal():
    adapter = PayscrowAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(400, {"message": "Payment method ID or payment type is required"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("100"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": None,
            },
            idempotency_key="x",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "bad_request"


# ─── cancel / notify_receipt — both no-ops ──────────────────


@pytest.mark.asyncio
async def test_cancel_request_is_noop_returns_true():
    """Payscrow has no cancel endpoint; we just return True."""
    adapter = PayscrowAdapter()
    p = _provider()
    # No HTTP patch — must not make a network call.
    ok = await adapter.cancel_request(
        provider=p, external_order_id="TRADE-1", timeout_ms=2000
    )
    assert ok is True


@pytest.mark.asyncio
async def test_notify_receipt_is_noop_returns_true():
    adapter = PayscrowAdapter()
    p = _provider()
    # No HTTP patch.
    ok = await adapter.notify_receipt(
        provider=p,
        external_order_id="TRADE-1",
        receipt_path="/whatever.jpg",
        comment="paid",
    )
    assert ok is True


# ─── raise_dispute (multipart with files[]) ─────────────────


@pytest.mark.asyncio
async def test_raise_dispute_fetches_amount_then_uploads_files():
    adapter = PayscrowAdapter()
    p = _provider()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh1:
        fh1.write(b"PDF 1")
        path1 = fh1.name
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh2:
        fh2.write(b"PNG 2")
        path2 = fh2.name

    def responder(req: _FakeRequest):
        if req.method == "GET" and req.url.endswith("/api/v1/order/TRADE-1"):
            return _FakeResponse(
                200,
                {
                    "success": True,
                    "order": {"id": "TRADE-1", "amount": "2500.00"},
                },
            )
        if req.method == "POST" and req.url.endswith("/api/v1/disputes/create"):
            assert req.headers["X-API-Key"] == API_KEY
            assert req.files_present is True
            assert req.form_data["order_id"] == "TRADE-1"
            assert req.form_data["amount"] == "2500.00"
            return _FakeResponse(
                201,
                {
                    "order_id": "TRADE-1",
                    "dispute_id": "8737378",
                    "dispute_files": ["x.pdf", "y.png"],
                },
            )
        raise AssertionError(f"unexpected call {req.method} {req.url}")

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.raise_dispute(
            provider=p,
            external_order_id="TRADE-1",
            reason="wrong amount",
            evidence_paths=[path1, path2],
        )
    assert ok is True


@pytest.mark.asyncio
async def test_raise_dispute_skips_when_order_amount_fetch_fails():
    adapter = PayscrowAdapter()
    p = _provider()

    def responder(_):
        # Both GET and POST hit the same handler; we deny the GET.
        return _FakeResponse(404, {})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.raise_dispute(
            provider=p,
            external_order_id="TRADE-X",
            reason="x",
            evidence_paths=[],
        )
    assert ok is False


# ─── parse_callback: token compare on X-API-Key ─────────────


def test_parse_callback_accepts_matching_api_key():
    adapter = PayscrowAdapter()
    p = _provider()
    payload = {
        "request_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
        "webhook_type": "buy_update",
        "datetime": "2024-08-02T15:34:07.440105",
        "payload": dict(_ORDER_RESPONSE, status="Completed"),
    }
    body = json.dumps(payload).encode()

    parsed = adapter.parse_callback(
        provider=p, headers={"X-API-Key": API_KEY}, body=body
    )
    assert parsed.external_order_id == _ORDER_RESPONSE["id"]
    assert parsed.status == ProviderStatus.SUCCESS
    assert parsed.paid_amount_fiat == Decimal("1000.00")


def test_parse_callback_rejects_wrong_api_key():
    adapter = PayscrowAdapter()
    p = _provider()
    payload = {"payload": {"id": "X", "status": "Completed"}}
    body = json.dumps(payload).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(
            provider=p, headers={"X-API-Key": "WRONG-KEY"}, body=body
        )


def test_parse_callback_missing_header():
    adapter = PayscrowAdapter()
    p = _provider()
    body = json.dumps({"payload": {"id": "X", "status": "Completed"}}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers={}, body=body)


def test_parse_callback_no_credential_skips_check():
    """When the terminal has no API key configured (dev mode), webhooks
    pass through unauthenticated — same convention as other adapters."""
    adapter = PayscrowAdapter()
    p = _provider(encrypted=False)
    body = json.dumps(
        {"payload": {"id": "X", "status": "Completed", "amount": "100"}}
    ).encode()
    parsed = adapter.parse_callback(provider=p, headers={}, body=body)
    assert parsed.status == ProviderStatus.SUCCESS


def test_parse_callback_all_states():
    adapter = PayscrowAdapter()
    p = _provider(encrypted=False)
    cases = {
        "Unpaid": ProviderStatus.PENDING,
        "Completed": ProviderStatus.SUCCESS,
        "CanceledByTimeout": ProviderStatus.EXPIRED,
        "CanceledByService": ProviderStatus.CANCELED,
    }
    for raw, expected in cases.items():
        body = json.dumps(
            {"payload": {"id": "X", "status": raw, "amount": "100"}}
        ).encode()
        parsed = adapter.parse_callback(provider=p, headers={}, body=body)
        assert parsed.status == expected, raw


def test_parse_callback_unknown_status_rejected():
    adapter = PayscrowAdapter()
    p = _provider(encrypted=False)
    body = json.dumps({"payload": {"id": "X", "status": "magic"}}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers={}, body=body)


def test_parse_callback_missing_id_rejected():
    adapter = PayscrowAdapter()
    p = _provider(encrypted=False)
    body = json.dumps({"payload": {"status": "Completed"}}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers={}, body=body)


# ─── poll_status ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_status_happy_path():
    adapter = PayscrowAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "GET"
        assert req.url.endswith("/api/v1/order/TRADE-1")
        return _FakeResponse(
            200,
            {"success": True, "order": {"id": "TRADE-1", "status": "Completed"}},
        )

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(
            provider=p, external_order_id="TRADE-1", timeout_ms=2000
        )
    assert parsed is not None
    assert parsed.status == ProviderStatus.SUCCESS


@pytest.mark.asyncio
async def test_poll_status_404_returns_none():
    adapter = PayscrowAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(404, {})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(
            provider=p, external_order_id="X", timeout_ms=2000
        )
    assert parsed is None


# ─── get_balance ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_balance_returns_available():
    adapter = PayscrowAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.url.endswith("/api/v1/finance/balance")
        return _FakeResponse(
            200,
            {
                "success": True,
                "deposit": {
                    "available": "4500.00",
                    "frozen": "500.00",
                    "total": "5000.00",
                    "currency": "RUB",
                },
            },
        )

    patcher, _ = _patch_httpx(responder)
    with patcher:
        balance = await adapter.get_balance(provider=p)
    # We surface ``available`` (spendable) instead of total.
    assert balance == Decimal("4500.00")


@pytest.mark.asyncio
async def test_get_balance_non_200_returns_none():
    adapter = PayscrowAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(503, {})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        balance = await adapter.get_balance(provider=p)
    assert balance is None


# ─── describe() ──────────────────────────────────────────────


def test_describe_supported_methods_and_no_provider_rate():
    info = PayscrowAdapter.describe()
    assert set(info.supported_methods) == {"sbp", "card"}
    assert info.supports_provider_rate is False


def test_describe_settings_schema():
    info = PayscrowAdapter.describe()
    keys = [f["key"] for f in info.settings_schema]
    assert "default_currency" in keys
    assert "unique_amount" in keys
    assert "method_type_map" in keys
    assert "nspk_code_map" in keys
