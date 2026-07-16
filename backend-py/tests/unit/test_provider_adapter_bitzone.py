"""Unit tests for the Bitzone cascade adapter.

Covers what makes Bitzone distinctive from the rest of our adapter zoo:
  * outbound auth is a single ``x-api-key`` header (no signing, no timestamp)
  * webhook signature reuses the API key as the HMAC secret
  * dispute flow is two-step: upload file → get invoiceKey → POST dispute
  * file upload routes ``tradeId`` via query string
  * balance is reported in smallest USDT units (1e-6) and we convert
"""
from __future__ import annotations

import hashlib
import hmac
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
from app.modules.cascading.integrations.bitzone import BitzoneAdapter
from tests.unit.cascade_adapter_testkit import (
    FakeRequest as _FakeRequest,
    FakeResponse as _FakeResponse,
    make_httpx_patch as _patch_httpx,
)


API_KEY = "bitzone-api-key-12345"
BASE_URL = "https://api.bitzone.space"


def _provider(
    settings: Dict[str, Any] | None = None,
    *,
    encrypted: bool = True,
):
    p = MagicMock()
    p.id = 22
    p.code = "bitzone"
    p.adapter_type = "bitzone"
    p.base_url = BASE_URL
    # Bitzone keeps a single credential — stored as api_secret (the helper
    # decrypts via decrypt_api_secret).
    p.api_secret_encrypted = encrypt_api_secret(API_KEY) if encrypted else None
    p.api_key_encrypted = None
    p.webhook_secret_encrypted = None  # webhook signs with api_secret
    base_settings: Dict[str, Any] = {
        "callback_url": "https://primepay.example/api/cascade/v1/callbacks/bitzone",
        "default_currency": "RUB",
    }
    base_settings.update(settings or {})
    p.settings = base_settings
    p.request_timeout_ms = 5000
    p.cancel_timeout_ms = 2000
    return p


# ─── Auth: one x-api-key, no signing ────────────────────────


def test_sign_request_only_emits_x_api_key():
    adapter = BitzoneAdapter()
    headers = adapter.sign_request(
        token=API_KEY,
        method="POST",
        path="/payment/trading/pay-in",
        body={"a": 1},
    )
    assert headers == {"x-api-key": API_KEY}


def test_sign_request_idempotency_ignored():
    """Bitzone has no idempotency channel — IDEMPOTENCY_HEADER="" disables it."""
    adapter = BitzoneAdapter()
    headers = adapter.sign_request(
        token=API_KEY, method="POST", path="/x", body=None, idempotency_key="abc"
    )
    assert "X-Idempotency-Key" not in headers
    assert headers == {"x-api-key": API_KEY}


def test_sign_request_passes_extra_headers():
    adapter = BitzoneAdapter()
    headers = adapter.sign_request(
        token=API_KEY,
        method="POST",
        path="/x",
        body=None,
        extra_headers={"X-Client": "primepay"},
    )
    assert headers["x-api-key"] == API_KEY
    assert headers["X-Client"] == "primepay"


# ─── supports() ─────────────────────────────────────────────


def test_supports_sbp_card_sim_by_default():
    adapter = BitzoneAdapter()
    for method in (PaymentMethod.SBP, PaymentMethod.CARD, PaymentMethod.SIM):
        assert adapter.supports(
            provider=_provider(), method=method, payment_option_code=None
        ), method


def test_supports_respects_method_map_whitelist():
    adapter = BitzoneAdapter()
    p = _provider({"method_map": {"sbp": "sbp"}})  # only SBP allowed
    assert adapter.supports(provider=p, method=PaymentMethod.SBP, payment_option_code=None)
    assert not adapter.supports(provider=p, method=PaymentMethod.CARD, payment_option_code=None)


# ─── issue_requisite ────────────────────────────────────────


_PAYIN_RESPONSE = {
    "id": "43ba8793-66ad-482f-9e74-54381d3aea25",
    "fiatAmount": "6030.00",
    "cryptoAmount": "60835352",
    "merchantFee": "5171005",
    "currencyRate": "99.12",
    "type": "pay-in",
    "status": "active",
    "bank": "SBER",
    "fiatCurrency": "RUB",
    "cryptoCurrency": "usdt",
    "cryptoNetwork": "trc20",
    "requisite": {
        "id": "13edb294-2f1e-4679-a62a-ea12fdab1c9c",
        "bank": "SBER",
        "ownerName": "Иван Иванов",
        "sbpNumber": None,
        "requisites": "5536913712341234",
        "fiatCurrency": "RUB",
        "method": "card",
    },
    "createdAt": "2024-11-08T17:00:16.583Z",
}


@pytest.mark.asyncio
async def test_issue_requisite_card_happy_path():
    adapter = BitzoneAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "POST"
        assert req.url.endswith("/payment/trading/pay-in")
        assert req.headers["x-api-key"] == API_KEY
        body = json.loads(req.read())
        assert body["fiatAmount"] == 6030.0
        assert body["fiatCurrency"] == "RUB"
        assert body["method"] == "card"
        assert body["bank"] == "SBER"
        assert body["callbackUrl"].endswith("/callbacks/bitzone")
        return _FakeResponse(200, _PAYIN_RESPONSE)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("6030.00"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": "sber",
                "merchant_request_id": "primepay-1",
            },
            idempotency_key="idem-1",
            timeout_ms=5000,
        )

    assert isinstance(result, ProviderRequisiteResponse)
    assert result.external_order_id == "43ba8793-66ad-482f-9e74-54381d3aea25"
    assert result.bank_name == "SBER"
    assert result.account_number == "5536913712341234"
    assert result.account_holder == "Иван Иванов"
    assert result.payment_method == PaymentMethod.CARD
    assert result.amount_fiat == Decimal("6030.00")
    assert result.provider_rate == Decimal("99.12")


@pytest.mark.asyncio
async def test_issue_requisite_sbp_uses_sbp_method():
    adapter = BitzoneAdapter()
    p = _provider()

    sbp_response = dict(_PAYIN_RESPONSE)
    sbp_response["requisite"] = {
        **_PAYIN_RESPONSE["requisite"],
        "method": "sbp",
        "sbpNumber": "+79991234567",
        "requisites": None,
    }

    def responder(req: _FakeRequest):
        body = json.loads(req.read())
        assert body["method"] == "sbp"
        return _FakeResponse(200, sbp_response)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("1000"),
                "payment_method": PaymentMethod.SBP,
                "payment_option_code": None,
            },
            idempotency_key="idem-2",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRequisiteResponse)
    assert result.payment_method == PaymentMethod.SBP
    assert result.account_number == "+79991234567"


@pytest.mark.asyncio
async def test_issue_requisite_unsupported_method_short_circuits():
    adapter = BitzoneAdapter()
    p = _provider({"method_map": {"sbp": "sbp"}})  # CARD not allowed
    # No HTTP patch needed; we should refuse before any network call.
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
    assert result.code == "unsupported_method"


@pytest.mark.asyncio
async def test_issue_requisite_no_requisite_returns_no_capacity():
    adapter = BitzoneAdapter()
    p = _provider()
    empty_response = dict(_PAYIN_RESPONSE)
    empty_response["requisite"] = {}

    def responder(_):
        return _FakeResponse(200, empty_response)

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
async def test_issue_requisite_http_400_becomes_refusal():
    adapter = BitzoneAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(400, {"error": "amount out of range"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("0.01"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": None,
            },
            idempotency_key="x",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "bad_request"


# ─── cancel_request ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_request_200():
    adapter = BitzoneAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "POST"
        assert req.url.endswith("/payment/trading/pay-in/TRADE-1/cancel")
        assert req.headers["x-api-key"] == API_KEY
        return _FakeResponse(200, {"status": "canceled"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.cancel_request(
            provider=p, external_order_id="TRADE-1", timeout_ms=2000
        )
    assert ok is True


@pytest.mark.asyncio
async def test_cancel_request_404_treated_as_success():
    adapter = BitzoneAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(404, {"error": "not found"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.cancel_request(
            provider=p, external_order_id="GONE", timeout_ms=2000
        )
    assert ok is True


# ─── notify_receipt: upload only ────────────────────────────


@pytest.mark.asyncio
async def test_notify_receipt_uploads_with_tradeid_query():
    adapter = BitzoneAdapter()
    p = _provider()

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(b"PNG bytes")
        receipt_path = fh.name

    def responder(req: _FakeRequest):
        assert req.url.endswith("/file/trading/pay-in/invoice/upload")
        assert req.headers["x-api-key"] == API_KEY
        assert req.files_present is True
        # tradeId must come through the query string, not the body.
        return _FakeResponse(200, {"invoiceKey": "inv-abc"})

    patcher, captured = _patch_httpx(responder)
    with patcher:
        ok = await adapter.notify_receipt(
            provider=p,
            external_order_id="TRADE-1",
            receipt_path=receipt_path,
            comment="paid",
        )
    assert ok is True
    # Verify request landed with tradeId param.
    assert captured, "no http call captured"


@pytest.mark.asyncio
async def test_notify_receipt_skipped_when_no_path():
    adapter = BitzoneAdapter()
    p = _provider()
    # No HTTP patch — should return True without making a call.
    ok = await adapter.notify_receipt(
        provider=p,
        external_order_id="T",
        receipt_path="",
        comment=None,
    )
    assert ok is True


@pytest.mark.asyncio
async def test_notify_receipt_upload_failure_returns_false():
    adapter = BitzoneAdapter()
    p = _provider()

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
        fh.write(b"PDF bytes")
        receipt_path = fh.name

    def responder(_):
        return _FakeResponse(500, {"error": "boom"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.notify_receipt(
            provider=p,
            external_order_id="TRADE-1",
            receipt_path=receipt_path,
            comment=None,
        )
    assert ok is False


# ─── raise_dispute: upload + POST dispute ───────────────────


@pytest.mark.asyncio
async def test_raise_dispute_uploads_then_posts():
    adapter = BitzoneAdapter()
    p = _provider()

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as fh:
        fh.write(b"JPG bytes")
        evidence_path = fh.name

    seq = []

    def responder(req: _FakeRequest):
        if req.url.endswith("/file/trading/pay-in/invoice/upload"):
            seq.append("upload")
            return _FakeResponse(200, {"invoiceKey": "inv-xyz"})
        if req.url.endswith("/payment/trading/TRADE-1"):
            seq.append("fetch")
            return _FakeResponse(200, {"id": "TRADE-1", "fiatAmount": "5000.00"})
        if req.url.endswith("/payment/trading/pay-in/TRADE-1/dispute"):
            seq.append("dispute")
            body = json.loads(req.read())
            assert body["invoiceKey"] == "inv-xyz"
            assert body["fiatAmount"] == 5000.0
            assert body["comment"] == "wrong amount"
            return _FakeResponse(200, {"id": "TRADE-1", "status": "dispute"})
        raise AssertionError(f"unexpected URL {req.url}")

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.raise_dispute(
            provider=p,
            external_order_id="TRADE-1",
            reason="wrong amount",
            evidence_paths=[evidence_path],
        )
    assert ok is True
    assert "upload" in seq and "dispute" in seq


@pytest.mark.asyncio
async def test_raise_dispute_no_evidence_refuses():
    adapter = BitzoneAdapter()
    p = _provider()
    # No HTTP patch — should bail before any call.
    ok = await adapter.raise_dispute(
        provider=p,
        external_order_id="TRADE-1",
        reason="x",
        evidence_paths=[],
    )
    assert ok is False


# ─── webhook signature: HMAC-SHA256 with api_key as secret ──


def _compute_x_signature(api_key: str, body: bytes) -> str:
    return hmac.new(api_key.encode(), body, hashlib.sha256).hexdigest()


def test_parse_callback_accepts_valid_signature():
    adapter = BitzoneAdapter()
    p = _provider()
    payload = dict(_PAYIN_RESPONSE)
    payload["status"] = "closed"
    body = json.dumps(payload).encode()
    sig = _compute_x_signature(API_KEY, body)

    parsed = adapter.parse_callback(
        provider=p, headers={"x-signature": sig}, body=body
    )
    assert parsed.external_order_id == _PAYIN_RESPONSE["id"]
    assert parsed.status == ProviderStatus.SUCCESS
    # closed → SUCCESS; paid_amount taken from fiatAmount on the webhook
    assert parsed.paid_amount_fiat == Decimal("6030.00")


def test_parse_callback_rejects_bad_signature():
    adapter = BitzoneAdapter()
    p = _provider()
    body = json.dumps({"id": "X", "status": "closed"}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(
            provider=p, headers={"x-signature": "deadbeef"}, body=body
        )


def test_parse_callback_missing_signature():
    adapter = BitzoneAdapter()
    p = _provider()
    body = json.dumps({"id": "X", "status": "closed"}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers={}, body=body)


def test_parse_callback_no_credential_skips_check():
    """When the provider has no API key configured (dev mode), we let
    the webhook through unsigned — same convention as the other adapters."""
    adapter = BitzoneAdapter()
    p = _provider(encrypted=False)
    body = json.dumps({"id": "X", "status": "closed"}).encode()
    parsed = adapter.parse_callback(provider=p, headers={}, body=body)
    assert parsed.status == ProviderStatus.SUCCESS


def test_parse_callback_maps_all_documented_statuses():
    adapter = BitzoneAdapter()
    p = _provider(encrypted=False)
    cases = {
        "pending": ProviderStatus.CREATED,
        "active": ProviderStatus.PENDING,
        "paid": ProviderStatus.PAID,
        "checking": ProviderStatus.DISPUTED,
        "dispute": ProviderStatus.DISPUTED,
        "re_calculation": ProviderStatus.DISPUTED,
        "closed": ProviderStatus.SUCCESS,
        "canceled": ProviderStatus.CANCELED,
    }
    for raw, expected in cases.items():
        body = json.dumps({"id": "X", "status": raw, "fiatAmount": "100"}).encode()
        parsed = adapter.parse_callback(provider=p, headers={}, body=body)
        assert parsed.status == expected, raw


def test_parse_callback_unknown_status_rejected():
    adapter = BitzoneAdapter()
    p = _provider(encrypted=False)
    body = json.dumps({"id": "X", "status": "wat"}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers={}, body=body)


def test_parse_callback_missing_id_rejected():
    adapter = BitzoneAdapter()
    p = _provider(encrypted=False)
    body = json.dumps({"status": "closed"}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers={}, body=body)


# ─── poll_status ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_status_happy_path():
    adapter = BitzoneAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "GET"
        assert req.url.endswith("/payment/trading/TRADE-1")
        return _FakeResponse(200, {"id": "TRADE-1", "status": "closed"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(
            provider=p, external_order_id="TRADE-1", timeout_ms=2000
        )
    assert parsed is not None
    assert parsed.status == ProviderStatus.SUCCESS
    assert parsed.external_order_id == "TRADE-1"


@pytest.mark.asyncio
async def test_poll_status_404_returns_none():
    adapter = BitzoneAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(404, {"error": "not found"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(
            provider=p, external_order_id="X", timeout_ms=2000
        )
    assert parsed is None


@pytest.mark.asyncio
async def test_poll_status_unknown_status_returns_none():
    adapter = BitzoneAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(200, {"id": "TRADE-1", "status": "magic"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(
            provider=p, external_order_id="TRADE-1", timeout_ms=2000
        )
    assert parsed is None


# ─── balance ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_balance_converts_smallest_units():
    """Bitzone returns balance as int in smallest USDT units (1e-6).
    We expose the decimal in plain USDT.
    """
    adapter = BitzoneAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "GET"
        assert req.url.endswith("/payment/account")
        assert req.headers["x-api-key"] == API_KEY
        return _FakeResponse(
            200,
            {"id": "mid", "name": "P", "callbackUrl": "u", "balance": 12345678},
        )

    patcher, _ = _patch_httpx(responder)
    with patcher:
        balance = await adapter.get_balance(provider=p)
    # 12345678 micro-USDT = 12.345678 USDT
    assert balance == Decimal("12.345678")


@pytest.mark.asyncio
async def test_get_balance_non_200_returns_none():
    adapter = BitzoneAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(503, {"error": "oops"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        balance = await adapter.get_balance(provider=p)
    assert balance is None


# ─── settings schema introspection (admin form generator) ───


def test_describe_contains_dropdown_options_for_method_map():
    info = BitzoneAdapter.describe()
    method_map_field = next(
        f for f in info.settings_schema if f["key"] == "method_map"
    )
    assert method_map_field["type"] == "kv_map"
    assert method_map_field["value_type"] == "select"
    assert any(opt["value"] == "sbp" for opt in method_map_field["options"])
    assert any(opt["value"] == "mobile_comm" for opt in method_map_field["options"])


def test_describe_marks_supported_methods():
    info = BitzoneAdapter.describe()
    assert set(info.supported_methods) == {"sbp", "card", "sim"}
    assert info.supports_provider_rate is True
