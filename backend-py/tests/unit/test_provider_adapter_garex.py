"""Unit tests for the Garex cascade adapter.

Garex has a few characteristics worth pinning down with tests:
  * Standard bearer auth (no signing), idempotency via orderId in the body.
  * One unified status-change endpoint (PATCH) covers cancel, mark-paid
    and dispute. Proof files travel as base64 data URLs inside the JSON
    body, not as multipart.
  * status=true/false envelope guards the actual ``result`` block — empty
    result means "no capacity".
  * Webhooks have no documented signature scheme — we expose the body's
    ``sign`` field via raw but skip verification.
"""
from __future__ import annotations

import base64
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
from app.modules.cascading.integrations.garex import GarexAdapter
from tests.unit.cascade_adapter_testkit import (
    FakeRequest as _FakeRequest,
    FakeResponse as _FakeResponse,
    make_httpx_patch as _patch_httpx,
)


TOKEN = "garex-bearer-token"
MERCHANT_ID = "mer123ch123bt123"
BASE_URL = "https://stage.garex.one"


def _provider(settings: Dict[str, Any] | None = None, *, encrypted: bool = True):
    p = MagicMock()
    p.id = 33
    p.code = "garex"
    p.adapter_type = "garex"
    p.base_url = BASE_URL
    p.api_secret_encrypted = encrypt_api_secret(TOKEN) if encrypted else None
    p.api_key_encrypted = None
    p.webhook_secret_encrypted = None
    base_settings: Dict[str, Any] = {
        "merchant_id": MERCHANT_ID,
        "callback_url": "https://primepay.example/api/cascade/v1/callbacks/garex",
        "default_currency": "RUB",
    }
    base_settings.update(settings or {})
    p.settings = base_settings
    p.request_timeout_ms = 5000
    p.cancel_timeout_ms = 2000
    return p


# ─── Auth: Bearer, no signing ───────────────────────────────


def test_sign_request_uses_bearer_no_signature():
    adapter = GarexAdapter()
    headers = adapter.sign_request(
        token=TOKEN, method="POST", path="/api/merchant/payments/payin", body={"a": 1}
    )
    assert headers == {"Authorization": f"Bearer {TOKEN}"}


def test_sign_request_idempotency_ignored():
    adapter = GarexAdapter()
    headers = adapter.sign_request(
        token=TOKEN, method="POST", path="/x", body=None, idempotency_key="abc"
    )
    assert "X-Idempotency-Key" not in headers


# ─── supports ────────────────────────────────────────────────


def test_supports_default_methods():
    adapter = GarexAdapter()
    for method in (PaymentMethod.SBP, PaymentMethod.CARD, PaymentMethod.SIM):
        assert adapter.supports(
            provider=_provider(), method=method, payment_option_code=None
        ), method


def test_supports_requires_merchant_id():
    adapter = GarexAdapter()
    p = _provider({"merchant_id": None})
    assert not adapter.supports(
        provider=p, method=PaymentMethod.CARD, payment_option_code=None
    )


def test_supports_respects_method_map_whitelist():
    adapter = GarexAdapter()
    p = _provider({"method_map": {"sbp": "sbp"}})
    assert adapter.supports(provider=p, method=PaymentMethod.SBP, payment_option_code=None)
    assert not adapter.supports(
        provider=p, method=PaymentMethod.CARD, payment_option_code=None
    )


# ─── issue_requisite ────────────────────────────────────────


_PAYIN_RESPONSE = {
    "status": True,
    "url": "https://stage.garex.one/payment/abc",
    "method": "sbp",
    "result": {
        "id": "9dcaa6a5-b4f8-45be-891e-159fee518481",
        "state": "pending",
        "amount": 1580,
        "rate": 102.918974,
        "address": "+79512992213",
        "bik": None,
        "recipient": "User 1 Offer 615 SBP Req T-bank",
        "bank": "t-bank",
        "bankName": "Т-банк",
        "orderId": "mer123ch123bt123Ord123er",
        "fee": 11.5,
        "sign": "",
        "usdt": 14.574572,
    },
}


@pytest.mark.asyncio
async def test_issue_requisite_happy_path():
    adapter = GarexAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "POST"
        assert req.url.endswith("/api/merchant/payments/payin")
        assert req.headers["Authorization"] == f"Bearer {TOKEN}"
        body = json.loads(req.read())
        assert body["orderId"] == "primepay-order-1"
        assert body["merchantId"] == MERCHANT_ID
        assert body["method"] == "sbp"
        assert body["assetOrBank"] == "t-bank"
        assert body["amount"] == 1580.0
        assert body["currency"] == "RUB"
        assert body["callbackUri"].endswith("/callbacks/garex")
        return _FakeResponse(200, _PAYIN_RESPONSE)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("1580"),
                "payment_method": PaymentMethod.SBP,
                "payment_option_code": "t-bank",
                "merchant_request_id": "primepay-order-1",
                "client_user_id": "user-42",
            },
            idempotency_key="idem-1",
            timeout_ms=5000,
        )

    assert isinstance(result, ProviderRequisiteResponse)
    assert result.external_order_id == "9dcaa6a5-b4f8-45be-891e-159fee518481"
    assert result.bank_name == "Т-банк"
    assert result.account_number == "+79512992213"
    assert result.account_holder == "User 1 Offer 615 SBP Req T-bank"
    assert result.payment_method == PaymentMethod.SBP
    assert result.amount_fiat == Decimal("1580")
    assert result.provider_rate == Decimal("102.918974")


@pytest.mark.asyncio
async def test_issue_requisite_card_uses_c2c_method():
    adapter = GarexAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        body = json.loads(req.read())
        assert body["method"] == "c2c"
        return _FakeResponse(200, _PAYIN_RESPONSE)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("1000"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": "sber",
            },
            idempotency_key="idem-c",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRequisiteResponse)


@pytest.mark.asyncio
async def test_issue_requisite_missing_merchant_id_short_circuits():
    adapter = GarexAdapter()
    p = _provider({"merchant_id": None})
    # No HTTP patch — must fail before any network call.
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
    assert result.code == "misconfigured"


@pytest.mark.asyncio
async def test_issue_requisite_missing_callback_url_short_circuits():
    adapter = GarexAdapter()
    p = _provider({"callback_url": ""})
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
    assert result.code == "misconfigured"


@pytest.mark.asyncio
async def test_issue_requisite_404_returns_no_capacity():
    adapter = GarexAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(404, {"status": False, "result": None})

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
async def test_issue_requisite_400_returns_no_capacity():
    """Garex 400 = no free requisite for the offer (still soft refusal)."""
    adapter = GarexAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(400, {"status": False})

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
async def test_issue_requisite_status_false_returns_refusal():
    adapter = GarexAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(200, {"status": False, "result": None})

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
async def test_issue_requisite_5xx_returns_refusal():
    adapter = GarexAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(500, {"error": "internal"})

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


# ─── cancel via PATCH status ────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_request_sends_patch_status_canceled():
    adapter = GarexAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "PATCH"
        assert req.url.endswith("/api/merchant/payments/TRADE-1/status")
        assert req.headers["Authorization"] == f"Bearer {TOKEN}"
        body = json.loads(req.read())
        assert body == {"status": "canceled"}
        return _FakeResponse(200, {"ok": True})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.cancel_request(
            provider=p, external_order_id="TRADE-1", timeout_ms=2000
        )
    assert ok is True


@pytest.mark.asyncio
async def test_cancel_request_404_treated_as_success():
    adapter = GarexAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(404, {"error": "not found"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.cancel_request(
            provider=p, external_order_id="GONE", timeout_ms=2000
        )
    assert ok is True


# ─── notify_receipt — base64 data URL in body ───────────────


@pytest.mark.asyncio
async def test_notify_receipt_sends_paid_status_with_base64_file():
    adapter = GarexAdapter()
    p = _provider()

    raw_bytes = b"JPEG bytes for receipt"
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as fh:
        fh.write(raw_bytes)
        receipt_path = fh.name

    expected_b64 = base64.b64encode(raw_bytes).decode("ascii")

    def responder(req: _FakeRequest):
        assert req.method == "PATCH"
        assert req.url.endswith("/api/merchant/payments/TRADE-1/status")
        body = json.loads(req.read())
        assert body["status"] == "paid"
        assert body["file"].startswith("data:image/jpeg;base64,")
        assert expected_b64 in body["file"]
        return _FakeResponse(200, {"ok": True})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.notify_receipt(
            provider=p,
            external_order_id="TRADE-1",
            receipt_path=receipt_path,
            comment="paid",
        )
    assert ok is True


@pytest.mark.asyncio
async def test_notify_receipt_without_path_still_marks_paid():
    """Garex's PATCH-status flow doesn't require a file."""
    adapter = GarexAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        body = json.loads(req.read())
        assert body == {"status": "paid"}  # no file key
        return _FakeResponse(200, {"ok": True})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.notify_receipt(
            provider=p,
            external_order_id="TRADE-1",
            receipt_path="",
            comment=None,
        )
    assert ok is True


# ─── raise_dispute ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_raise_dispute_sends_dispute_status_with_file():
    adapter = GarexAdapter()
    p = _provider()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(b"PDF bytes")
        evidence_path = fh.name

    def responder(req: _FakeRequest):
        body = json.loads(req.read())
        assert body["status"] == "dispute"
        assert body["file"].startswith("data:application/pdf;base64,")
        return _FakeResponse(200, {"ok": True})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.raise_dispute(
            provider=p,
            external_order_id="TRADE-1",
            reason="wrong amount",
            evidence_paths=[evidence_path],
        )
    assert ok is True


@pytest.mark.asyncio
async def test_raise_dispute_without_evidence_still_sends_dispute():
    adapter = GarexAdapter()
    p = _provider()

    seq = []

    def responder(req: _FakeRequest):
        body = json.loads(req.read())
        seq.append(body)
        return _FakeResponse(200, {"ok": True})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.raise_dispute(
            provider=p,
            external_order_id="TRADE-1",
            reason="x",
            evidence_paths=[],
        )
    assert ok is True
    assert seq == [{"status": "dispute"}]


# ─── parse_callback ──────────────────────────────────────────


def test_parse_callback_finished_status():
    adapter = GarexAdapter()
    p = _provider()
    payload = {
        "id": "9dcaa6a5-b4f8-45be-891e-159fee518481",
        "state": "finished",
        "amount": 1580,
        "rate": 102.918974,
        "address": "+79512992213",
        "bank": "t-bank",
        "bankName": "Т-банк",
        "sign": "",
        "orderId": "mer123ch123bt123Ord123er",
        "fee": 11.5,
        "usdt": 14.574572,
        "check": "data:image/pdf;base64,JVB...",
    }
    body = json.dumps(payload).encode()
    parsed = adapter.parse_callback(provider=p, headers={}, body=body)
    assert parsed.external_order_id == "9dcaa6a5-b4f8-45be-891e-159fee518481"
    assert parsed.status == ProviderStatus.SUCCESS
    assert parsed.paid_amount_fiat == Decimal("1580")
    # The sign field is exposed via raw for downstream audit.
    assert parsed.raw["sign"] == ""


def test_parse_callback_all_states():
    adapter = GarexAdapter()
    p = _provider()
    cases = {
        "created": ProviderStatus.CREATED,
        "pending": ProviderStatus.PENDING,
        "paid": ProviderStatus.PAID,
        "finished": ProviderStatus.SUCCESS,
        "canceled": ProviderStatus.CANCELED,
        "dispute": ProviderStatus.DISPUTED,
        "failed": ProviderStatus.FAILED,
    }
    for raw, expected in cases.items():
        body = json.dumps({"id": "X", "state": raw, "amount": 100}).encode()
        parsed = adapter.parse_callback(provider=p, headers={}, body=body)
        assert parsed.status == expected, raw


def test_parse_callback_unknown_state_rejected():
    adapter = GarexAdapter()
    p = _provider()
    body = json.dumps({"id": "X", "state": "magic"}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers={}, body=body)


def test_parse_callback_missing_id_rejected():
    adapter = GarexAdapter()
    p = _provider()
    body = json.dumps({"state": "finished"}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers={}, body=body)


# ─── poll_status ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_status_via_post():
    adapter = GarexAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "POST"
        assert req.url.endswith("/api/merchant/payments/status")
        body = json.loads(req.read())
        assert body == {"merchantId": MERCHANT_ID, "paymentId": "TRADE-1"}
        return _FakeResponse(200, {"id": "TRADE-1", "state": "finished"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(
            provider=p, external_order_id="TRADE-1", timeout_ms=2000
        )
    assert parsed is not None
    assert parsed.status == ProviderStatus.SUCCESS


@pytest.mark.asyncio
async def test_poll_status_handles_result_envelope():
    """Garex sometimes returns the payment inside a `result` block."""
    adapter = GarexAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(
            200, {"status": True, "result": {"id": "TRADE-1", "state": "pending"}}
        )

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(
            provider=p, external_order_id="TRADE-1", timeout_ms=2000
        )
    assert parsed is not None
    assert parsed.status == ProviderStatus.PENDING


@pytest.mark.asyncio
async def test_poll_status_404_returns_none():
    adapter = GarexAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(404, {})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(
            provider=p, external_order_id="X", timeout_ms=2000
        )
    assert parsed is None


# ─── balance / rate ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_balance_returns_amount():
    adapter = GarexAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "GET"
        assert req.url.endswith("/api/merchant/balance")
        assert req.headers["Authorization"] == f"Bearer {TOKEN}"
        return _FakeResponse(200, {"amount": 5724.89512})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        balance = await adapter.get_balance(provider=p)
    assert balance == Decimal("5724.89512")


@pytest.mark.asyncio
async def test_get_balance_non_200_returns_none():
    adapter = GarexAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(503, {})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        balance = await adapter.get_balance(provider=p)
    assert balance is None


@pytest.mark.asyncio
async def test_get_upstream_rate_returns_rate():
    adapter = GarexAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.url.endswith("/api/merchant/rate")
        return _FakeResponse(200, {"rate": 80.58})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        rate = await adapter.get_upstream_rate(provider=p)
    assert rate == Decimal("80.58")


# ─── describe() ──────────────────────────────────────────────


def test_describe_lists_settings_fields():
    info = GarexAdapter.describe()
    keys = [f["key"] for f in info.settings_schema]
    assert "merchant_id" in keys
    assert "callback_url" in keys
    assert "method_map" in keys
    assert "bank_code_map" in keys


def test_describe_supported_methods():
    info = GarexAdapter.describe()
    assert set(info.supported_methods) == {"sbp", "card", "sim"}
    assert info.supports_provider_rate is True
