"""Unit tests for the BridgePay cascade adapter.

Focus on the patterns that distinguish BridgePay from our other adapters:
  * Dual-credential auth — X-Identity (api_key) + X-Signature (secret)
  * Signing string = METHOD + FULL_URL + RAW_BODY (no timestamp)
  * GET / multipart sign without body, JSON signs raw compact JSON
  * Webhook = X-Notification-Token bare compare, not HMAC
  * Balance is a list of accounts — adapter filters USDT
  * Dispute requires fetching invoice first to extract dealId
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import tempfile
from decimal import Decimal
from typing import Any, Callable, Dict, List
from unittest.mock import MagicMock, patch

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
from app.modules.cascading.integrations.bridgepay import BridgePayAdapter
from tests.unit.cascade_adapter_testkit import (
    FakeRequest as _FakeRequest,
    FakeResponse as _FakeResponse,
    make_httpx_patch as _patch_httpx,
)


TEST_API_KEY = "bp-api-key-xyz"
TEST_SECRET = "bp-signing-secret"
TEST_NOTIFICATION_TOKEN = "bp-notification-token"
BASE_URL = "https://api.blacklemon.pro"


def _provider(
    settings: Dict[str, Any] | None = None,
    *,
    with_api_key: bool = True,
    with_secret: bool = True,
    with_notification_token: bool = True,
):
    p = MagicMock()
    p.id = 21
    p.code = "bridgepay"
    p.adapter_type = "bridgepay"
    p.base_url = BASE_URL
    p.api_key_encrypted = encrypt_api_secret(TEST_API_KEY) if with_api_key else None
    p.api_secret_encrypted = encrypt_api_secret(TEST_SECRET) if with_secret else None
    p.webhook_secret_encrypted = (
        encrypt_api_secret(TEST_NOTIFICATION_TOKEN) if with_notification_token else None
    )
    base_settings = {
        "notification_url": "https://us/api/cascade/v1/callbacks/bridgepay",
        "default_currency": "RUB",
        "default_payment_option": "TO_CARD",
    }
    base_settings.update(settings or {})
    p.settings = base_settings
    p.request_timeout_ms = 5000
    p.cancel_timeout_ms = 2000
    return p


# Fake httpx imported from cascade_adapter_testkit at the top.


# ─── Signing primitives ──────────────────────────────────────


def _expected_signature(method: str, url: str, body_str: str, secret: str) -> str:
    payload = f"{method}{url}{body_str}"
    digest = hmac.new(secret.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def test_sign_request_json_body_includes_raw_compact_json():
    adapter = BridgePayAdapter()
    p = _provider()
    body = {"type": "in", "amount": "1000", "currency": "RUB"}
    headers = adapter.sign_request(
        token=TEST_SECRET,
        method="POST",
        path="/api/merchant/invoices",
        body=body,
        provider=p,
    )
    assert headers["X-Identity"] == TEST_API_KEY
    expected = _expected_signature(
        "POST",
        f"{BASE_URL}/api/merchant/invoices",
        '{"type":"in","amount":"1000","currency":"RUB"}',
        TEST_SECRET,
    )
    assert headers["X-Signature"] == expected


def test_sign_request_get_omits_body():
    adapter = BridgePayAdapter()
    p = _provider()
    headers = adapter.sign_request(
        token=TEST_SECRET,
        method="GET",
        path="/api/merchant/accounts",
        body=None,
        provider=p,
    )
    expected = _expected_signature(
        "GET", f"{BASE_URL}/api/merchant/accounts", "", TEST_SECRET
    )
    assert headers["X-Signature"] == expected


def test_sign_request_multipart_omits_body():
    """For multipart calls we pass body=None so signing only covers method + URL."""
    adapter = BridgePayAdapter()
    p = _provider()
    path = "/api/merchant/invoices/abc/confirm-transfer"
    headers = adapter.sign_request(
        token=TEST_SECRET, method="POST", path=path, body=None, provider=p
    )
    expected = _expected_signature("POST", f"{BASE_URL}{path}", "", TEST_SECRET)
    assert headers["X-Signature"] == expected


def test_sign_request_requires_provider():
    adapter = BridgePayAdapter()
    with pytest.raises(CallbackVerificationError):
        adapter.sign_request(
            token=TEST_SECRET, method="GET", path="/x", body=None, provider=None
        )


def test_sign_request_requires_api_key():
    adapter = BridgePayAdapter()
    p = _provider(with_api_key=False)
    with pytest.raises(CallbackVerificationError):
        adapter.sign_request(
            token=TEST_SECRET, method="GET", path="/x", body=None, provider=p
        )


# ─── supports() ─────────────────────────────────────────────


def test_supports_sbp_card_sim():
    adapter = BridgePayAdapter()
    p = _provider()
    assert adapter.supports(provider=p, method=PaymentMethod.SBP, payment_option_code=None)
    assert adapter.supports(provider=p, method=PaymentMethod.CARD, payment_option_code=None)
    assert adapter.supports(provider=p, method=PaymentMethod.SIM, payment_option_code=None)


def test_supports_respects_payment_option_whitelist():
    adapter = BridgePayAdapter()
    p = _provider({"payment_option_map": {"sbp": "SBP"}})  # closed whitelist
    assert adapter.supports(provider=p, method=PaymentMethod.SBP, payment_option_code=None)
    assert not adapter.supports(provider=p, method=PaymentMethod.CARD, payment_option_code=None)


# ─── issue_requisite ────────────────────────────────────────


_INVOICE_RESPONSE = {
    "id": "991a0e40-8cbd-405a-80fa-c84f2e4d5c6a",
    "internalId": "00001",
    "type": "in",
    "status": "new",
    "paymentMethod": "sberbank",
    "paymentOption": None,
    "sum": {"amount": "1000.00", "currency": "RUB", "subunit": 2},
    "createdAt": "2024-11-04T13:55:45+00:00",
    "expireAt": "2024-11-04T14:55:45+00:00",
    "deals": [
        {
            "id": "595af0e7-261d-4451-b651-3cfeecc0755c",
            "type": "in",
            "status": "transfer_waiting",
            "paymentMethodName": "Сбербанк",
            "paymentMethod": "sberbank",
            "paymentOption": "SBP",
            "requisites": {"requisites": "+7(996)777-99-77", "holder": "Maxim B."},
            "rate": "99.23",
            "isActive": True,
        }
    ],
}


@pytest.mark.asyncio
async def test_issue_requisite_happy_path():
    adapter = BridgePayAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        # Auth headers set correctly.
        assert req.headers["X-Identity"] == TEST_API_KEY
        # Signature verifiable against the raw body we actually shipped.
        sent_body = req.read()
        expected_sig = _expected_signature(
            "POST",
            f"{BASE_URL}/api/merchant/invoices",
            sent_body.decode(),
            TEST_SECRET,
        )
        assert req.headers["X-Signature"] == expected_sig
        # Body fields match the BridgePay contract.
        body = json.loads(sent_body)
        assert body["type"] == "in"
        assert body["currency"] == "RUB"
        assert body["paymentOption"] == "SBP"
        assert body["paymentMethod"] is None
        assert body["startDeal"] is True
        assert body["notificationToken"] == TEST_NOTIFICATION_TOKEN
        assert body["notificationUrl"].endswith("/cascade/v1/callbacks/bridgepay")
        return _FakeResponse(200, _INVOICE_RESPONSE)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("1000.00"),
                "payment_method": PaymentMethod.SBP,
                "payment_option_code": None,
                "client_user_id": "uid-1",
                "merchant_request_id": "00001",
            },
            idempotency_key="idem-1",
            timeout_ms=5000,
        )

    assert isinstance(result, ProviderRequisiteResponse)
    assert result.external_order_id == "991a0e40-8cbd-405a-80fa-c84f2e4d5c6a"
    assert result.account_number == "+7(996)777-99-77"
    assert result.account_holder == "Maxim B."
    assert result.payment_method == PaymentMethod.SBP  # from paymentOption=SBP
    assert result.amount_fiat == Decimal("1000.00")
    assert result.provider_rate == Decimal("99.23")


@pytest.mark.asyncio
async def test_issue_requisite_passes_bank_code_through_payment_method():
    adapter = BridgePayAdapter()
    p = _provider({"payment_method_map": {"sber": "sberbank", "tinkoff": "tinkoff"}})

    def responder(req: _FakeRequest):
        body = json.loads(req.read())
        assert body["paymentMethod"] == "sberbank"
        return _FakeResponse(200, _INVOICE_RESPONSE)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("1000"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": "sber",
            },
            idempotency_key="x",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRequisiteResponse)


@pytest.mark.asyncio
async def test_issue_requisite_empty_deals_returns_refusal():
    adapter = BridgePayAdapter()
    p = _provider()
    response = {**_INVOICE_RESPONSE, "deals": []}

    def responder(_):
        return _FakeResponse(200, response)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("1000"),
                "payment_method": PaymentMethod.SBP,
                "payment_option_code": None,
            },
            idempotency_key="x",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "no_capacity"


@pytest.mark.asyncio
async def test_issue_requisite_4xx_returns_typed_refusal():
    adapter = BridgePayAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(403, {"error": "IP not whitelisted"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("1000"),
                "payment_method": PaymentMethod.SBP,
                "payment_option_code": None,
            },
            idempotency_key="x",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "forbidden"


@pytest.mark.asyncio
async def test_issue_requisite_missing_api_key_short_circuits():
    adapter = BridgePayAdapter()
    p = _provider(with_api_key=False)
    result = await adapter.issue_requisite(
        provider=p,
        order_data={
            "amount": Decimal("1000"),
            "payment_method": PaymentMethod.SBP,
            "payment_option_code": None,
        },
        idempotency_key="x",
        timeout_ms=5000,
    )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "misconfigured"


# ─── cancel ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_request_signs_without_body():
    adapter = BridgePayAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        expected_sig = _expected_signature(
            "POST",
            f"{BASE_URL}/api/merchant/invoices/INV-1/cancel",
            "",
            TEST_SECRET,
        )
        assert req.headers["X-Signature"] == expected_sig
        return _FakeResponse(200, {"id": "INV-1", "status": "canceled", "deals": []})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.cancel_request(
            provider=p, external_order_id="INV-1", timeout_ms=2000
        )
    assert ok is True


@pytest.mark.asyncio
async def test_cancel_request_404_treated_as_success():
    adapter = BridgePayAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(404, {"error": "not found"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.cancel_request(
            provider=p, external_order_id="X", timeout_ms=2000
        )
    assert ok is True


# ─── notify_receipt (multipart) ─────────────────────────────


@pytest.mark.asyncio
async def test_notify_receipt_uploads_attachment():
    adapter = BridgePayAdapter()
    p = _provider()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(b"PNG")
        receipt_path = fh.name

    def responder(req: _FakeRequest):
        assert req.method == "POST"
        assert req.url.endswith("/api/merchant/invoices/INV-1/confirm-transfer")
        # Multipart → body in signature is empty.
        expected_sig = _expected_signature(
            "POST",
            f"{BASE_URL}/api/merchant/invoices/INV-1/confirm-transfer",
            "",
            TEST_SECRET,
        )
        assert req.headers["X-Signature"] == expected_sig
        assert req.files_present is True
        return _FakeResponse(200, {"id": "INV-1"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.notify_receipt(
            provider=p,
            external_order_id="INV-1",
            receipt_path=receipt_path,
            comment=None,
        )
    assert ok is True


# ─── dispute (fetch invoice → submit) ───────────────────────


@pytest.mark.asyncio
async def test_raise_dispute_fetches_deal_id_then_submits():
    adapter = BridgePayAdapter()
    p = _provider()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(b"PNG")
        evidence_path = fh.name

    calls: List[_FakeRequest] = []

    def responder(req: _FakeRequest):
        calls.append(req)
        if req.method == "GET":
            return _FakeResponse(200, _INVOICE_RESPONSE)
        # second call — POST /dispute multipart
        assert "dispute" in req.url
        assert req.form_data["dealId"] == "595af0e7-261d-4451-b651-3cfeecc0755c"
        assert req.form_data["disputeReason"] == "no_payment"
        assert req.files_present is True
        return _FakeResponse(200, {})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.raise_dispute(
            provider=p,
            external_order_id="INV-1",
            reason="no_payment",
            evidence_paths=[evidence_path],
        )
    assert ok is True
    assert len(calls) == 2
    assert calls[0].method == "GET"
    assert calls[1].method == "POST"


@pytest.mark.asyncio
async def test_raise_dispute_no_active_deal_returns_false():
    adapter = BridgePayAdapter()
    p = _provider()
    invoice_no_deals = {**_INVOICE_RESPONSE, "deals": []}

    def responder(_):
        return _FakeResponse(200, invoice_no_deals)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.raise_dispute(
            provider=p,
            external_order_id="INV-1",
            reason="no_payment",
            evidence_paths=[],
        )
    assert ok is False


# ─── webhook: X-Notification-Token compare ─────────────────


def test_parse_callback_accepts_valid_token():
    adapter = BridgePayAdapter()
    p = _provider()
    payload = {
        "id": "991a0e40-8cbd-405a-80fa-c84f2e4d5c6a",
        "internalId": "00001",
        "status": "paid",
        "sum": {"amount": "1000.00", "currency": "RUB", "subunit": 2},
        "notificationType": "invoice",
    }
    body = json.dumps(payload).encode()
    parsed = adapter.parse_callback(
        provider=p,
        headers={"X-Notification-Token": TEST_NOTIFICATION_TOKEN},
        body=body,
    )
    assert parsed.external_order_id == "991a0e40-8cbd-405a-80fa-c84f2e4d5c6a"
    assert parsed.status == ProviderStatus.SUCCESS
    assert parsed.paid_amount_fiat == Decimal("1000.00")


def test_parse_callback_rejects_bad_token():
    adapter = BridgePayAdapter()
    p = _provider()
    body = json.dumps({"id": "X", "status": "new"}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(
            provider=p,
            headers={"X-Notification-Token": "wrong"},
            body=body,
        )


def test_parse_callback_rejects_missing_token_header():
    adapter = BridgePayAdapter()
    p = _provider()
    body = json.dumps({"id": "X", "status": "new"}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers={}, body=body)


def test_parse_callback_no_token_configured_skips_check():
    adapter = BridgePayAdapter()
    p = _provider(with_notification_token=False)
    body = json.dumps({"id": "X", "status": "new"}).encode()
    parsed = adapter.parse_callback(provider=p, headers={}, body=body)
    assert parsed.status == ProviderStatus.PENDING


def test_parse_callback_maps_all_documented_statuses():
    adapter = BridgePayAdapter()
    p = _provider(with_notification_token=False)
    cases = {
        "new": ProviderStatus.PENDING,
        "transfer_waiting": ProviderStatus.PENDING,
        "transfer_confirmed": ProviderStatus.PAID,
        "paid": ProviderStatus.SUCCESS,
        "completed": ProviderStatus.SUCCESS,
        "dispute": ProviderStatus.DISPUTED,
        "canceled": ProviderStatus.CANCELED,
        "cancelled": ProviderStatus.CANCELED,
        "expired": ProviderStatus.EXPIRED,
    }
    for raw, expected in cases.items():
        body = json.dumps({"id": "X", "status": raw}).encode()
        parsed = adapter.parse_callback(provider=p, headers={}, body=body)
        assert parsed.status == expected, raw


def test_parse_callback_unknown_status_rejected():
    adapter = BridgePayAdapter()
    p = _provider(with_notification_token=False)
    body = json.dumps({"id": "X", "status": "magic"}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers={}, body=body)


# ─── balance ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_balance_finds_usdt_account():
    adapter = BridgePayAdapter()
    p = _provider()
    accounts = [
        {
            "id": "df2b175e",
            "currency": "USDT",
            "sum": {"amount": "10.74886973", "currency": "USDT", "subunit": 8},
            "frozenSum": {"amount": "0.00000000", "currency": "USDT", "subunit": 8},
        },
    ]

    def responder(req: _FakeRequest):
        assert req.method == "GET"
        assert req.url.endswith("/api/merchant/accounts")
        return _FakeResponse(200, accounts)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        balance = await adapter.get_balance(provider=p)
    assert balance == Decimal("10.74886973")


@pytest.mark.asyncio
async def test_get_balance_no_usdt_account_returns_none():
    adapter = BridgePayAdapter()
    p = _provider()
    accounts = [
        {"id": "x", "currency": "RUB", "sum": {"amount": "100", "currency": "RUB", "subunit": 2}}
    ]

    def responder(_):
        return _FakeResponse(200, accounts)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        balance = await adapter.get_balance(provider=p)
    assert balance is None


@pytest.mark.asyncio
async def test_get_balance_non_200_returns_none():
    adapter = BridgePayAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(503, {"error": "unavailable"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        balance = await adapter.get_balance(provider=p)
    assert balance is None


# ─── poll_status ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_status_happy_path():
    adapter = BridgePayAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        return _FakeResponse(200, {"id": "INV-1", "status": "paid"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(
            provider=p, external_order_id="INV-1", timeout_ms=2000
        )
    assert parsed is not None
    assert parsed.status == ProviderStatus.SUCCESS


@pytest.mark.asyncio
async def test_poll_status_unknown_status_returns_none():
    adapter = BridgePayAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(200, {"id": "X", "status": "weird"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(
            provider=p, external_order_id="X", timeout_ms=2000
        )
    assert parsed is None
