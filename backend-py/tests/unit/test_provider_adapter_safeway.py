"""Unit tests for the Safeway cascade adapter.

Covers what makes Safeway distinctive from the rest of our adapter zoo:
  * outbound auth is a single ``Access-Token`` header + mandatory ``Accept``
    (no signing, no timestamp, no idempotency channel)
  * H2H create: ``payment_gateway`` when a bank is pinned, else ``currency``;
    amount is an integer; the response carries ``conversion_price`` (provider rate)
  * the ``{success, data}`` envelope (``success:false`` → refusal)
  * receipts are forwarded **non-destructively** — ``notify_receipt`` resolves our
    external_id then ``POST .../{external_id}/receipt``; disputes stay on ``.../dispute``
  * webhooks are UNSIGNED and may arrive bare or wrapped in ``{data:{...}}``
  * status mapping reads ONLY the coarse ``status`` field (sub_status ignored)
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
from datetime import datetime, timezone
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
from app.modules.cascading.integrations.safeway import SafewayAdapter
from tests.unit.cascade_adapter_testkit import (
    FakeRequest as _FakeRequest,
    FakeResponse as _FakeResponse,
    make_httpx_patch as _patch_httpx,
)


TOKEN = "safeway-access-token-xyz"
MERCHANT_ID = "11111111-2222-3333-4444-555555555555"
BASE_URL = "https://api.safeway.example"


def _provider(
    settings: Dict[str, Any] | None = None,
    *,
    encrypted: bool = True,
):
    p = MagicMock()
    p.id = 77
    p.code = "safeway"
    p.adapter_type = "safeway"
    p.base_url = BASE_URL
    p.api_secret_encrypted = encrypt_api_secret(TOKEN) if encrypted else None
    p.api_key_encrypted = None
    p.webhook_secret_encrypted = None  # unsigned webhooks
    base_settings: Dict[str, Any] = {
        "merchant_id": MERCHANT_ID,
        "default_currency": "rub",
        "callback_url": "https://primepay.example/api/cascade/v1/callbacks/safeway",
    }
    base_settings.update(settings or {})
    p.settings = base_settings
    p.request_timeout_ms = 5000
    p.cancel_timeout_ms = 2000
    return p


# H2H create / poll / webhook payload (the `data` object).
_H2H_DATA: Dict[str, Any] = {
    "order_id": "ord-abc-123",
    "external_id": "primepay-1",
    "amount": 6030,
    "status": "pending",
    "sub_status": "waiting_for_payment",
    "payment_gateway": "sberbank_rub",
    "payment_gateway_name": "Сбербанк",
    "payment_detail": {
        "detail": "5536913712341234",
        "detail_type": "card",
        "initials": "Иван И.",
    },
    "conversion_price": "97.50",
    "expires_at": 1750000000,
}


def _ok(data: Dict[str, Any]) -> Dict[str, Any]:
    return {"success": True, "data": data}


# ─── Auth: Access-Token + Accept, no signing ────────────────


def test_sign_request_emits_access_token_and_accept():
    adapter = SafewayAdapter()
    headers = adapter.sign_request(
        token=TOKEN, method="POST", path="/api/h2h/order", body={"a": 1}
    )
    assert headers["Access-Token"] == TOKEN
    assert headers["Accept"] == "application/json"


def test_sign_request_ignores_idempotency_and_signature():
    adapter = SafewayAdapter()
    headers = adapter.sign_request(
        token=TOKEN, method="POST", path="/x", body=None, idempotency_key="abc"
    )
    assert "X-Idempotency-Key" not in headers
    assert "X-Signature" not in headers
    assert headers == {"Access-Token": TOKEN, "Accept": "application/json"}


def test_sign_request_passes_extra_headers():
    adapter = SafewayAdapter()
    headers = adapter.sign_request(
        token=TOKEN, method="POST", path="/x", body=None,
        extra_headers={"X-Client": "primepay"},
    )
    assert headers["Access-Token"] == TOKEN
    assert headers["X-Client"] == "primepay"


# ─── supports() ─────────────────────────────────────────────


def test_supports_sbp_and_card_by_default():
    adapter = SafewayAdapter()
    for method in (PaymentMethod.SBP, PaymentMethod.CARD):
        assert adapter.supports(provider=_provider(), method=method, payment_option_code=None), method


def test_supports_respects_method_map_whitelist():
    adapter = SafewayAdapter()
    p = _provider({"method_map": {"sbp": "phone"}})  # only SBP allowed
    assert adapter.supports(provider=p, method=PaymentMethod.SBP, payment_option_code=None)
    assert not adapter.supports(provider=p, method=PaymentMethod.CARD, payment_option_code=None)


# ─── issue_requisite ────────────────────────────────────────


@pytest.mark.asyncio
async def test_issue_requisite_card_with_pinned_bank():
    adapter = SafewayAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "POST"
        assert req.url.endswith("/api/h2h/order")
        assert req.headers["Access-Token"] == TOKEN
        assert req.headers["Accept"] == "application/json"
        body = json.loads(req.read())
        assert body["amount"] == 6030 and isinstance(body["amount"], int)
        assert body["merchant_id"] == MERCHANT_ID
        assert body["payment_detail_type"] == "card"
        # We send OUR internal order uuid — never the merchant's external_id.
        assert body["external_id"] == "our-order-uuid-1"
        assert body["external_id"] != "bot_8797443492_ac2c"  # merchant id not leaked
        assert body["payment_gateway"] == "sberbank_rub"  # bank pinned
        assert "currency" not in body                      # mutually exclusive
        assert body["callback_url"].endswith("/callbacks/safeway")
        return _FakeResponse(200, _ok(_H2H_DATA))

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("6030.00"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": "sber",
                "uuid": "our-order-uuid-1",            # OUR internal order id
                "external_id": "bot_8797443492_ac2c",  # merchant's id — must NOT leak
            },
            idempotency_key="idem-1",
            timeout_ms=5000,
        )

    assert isinstance(result, ProviderRequisiteResponse)
    assert result.external_order_id == "ord-abc-123"
    assert result.account_number == "5536913712341234"
    assert result.account_holder == "Иван И."
    assert result.bank_name == "Сбербанк"
    assert result.payment_method == PaymentMethod.CARD
    assert result.amount_fiat == Decimal("6030")
    assert result.provider_rate == Decimal("97.50")
    assert result.expires_at == datetime.fromtimestamp(1750000000, tz=timezone.utc)


def test_build_payin_request_sends_our_uuid_never_merchant_id():
    """The provider-facing external_id is OUR order.uuid — the merchant's
    external_id / request id are never forwarded (no merchant-id leak). Falls
    back to the idempotency_key only on order-less paths (admin probe / CLI)."""
    adapter = SafewayAdapter()
    req = adapter.build_payin_request(
        provider=_provider(),
        order_data={
            "amount": Decimal("1000"),
            "payment_method": PaymentMethod.SBP,
            "uuid": "order-uuid-xyz",
            "external_id": "bot_8797443492_ac2c58415e93",  # merchant's id
            "merchant_request_id": "merch-req-9",          # also not ours to send
        },
        idempotency_key="idem-9",
        method=PaymentMethod.SBP,
        method_value="phone",
    )
    assert req.body["external_id"] == "order-uuid-xyz"
    assert req.body["external_id"] not in {"bot_8797443492_ac2c58415e93", "merch-req-9"}

    # Order-less path (admin probe): no uuid → fall back to our idempotency_key.
    req2 = adapter.build_payin_request(
        provider=_provider(),
        order_data={"amount": Decimal("1000"), "payment_method": PaymentMethod.SBP},
        idempotency_key="idem-9",
        method=PaymentMethod.SBP,
        method_value="phone",
    )
    assert req2.body["external_id"] == "idem-9"


@pytest.mark.asyncio
async def test_issue_requisite_sbp_without_bank_sends_currency():
    adapter = SafewayAdapter()
    p = _provider()

    sbp_data = {
        **_H2H_DATA,
        "payment_gateway": "sbp_any",
        "payment_gateway_name": "СБП",
        "payment_detail": {"detail": "+79991234567", "detail_type": "phone", "initials": "Пётр П."},
    }

    def responder(req: _FakeRequest):
        body = json.loads(req.read())
        assert body["payment_detail_type"] == "phone"
        assert body["currency"] == "rub"        # no bank → currency scope
        assert "payment_gateway" not in body
        return _FakeResponse(200, _ok(sbp_data))

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
    assert result.account_number == "+79991234567"
    assert result.payment_method == PaymentMethod.SBP


@pytest.mark.asyncio
async def test_issue_requisite_unknown_bank_falls_back_to_currency():
    adapter = SafewayAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        body = json.loads(req.read())
        assert "payment_gateway" not in body
        assert body["currency"] == "rub"
        return _FakeResponse(200, _ok(_H2H_DATA))

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("6030"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": "some_unmapped_bank",
            },
            idempotency_key="idem-3",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRequisiteResponse)


@pytest.mark.asyncio
async def test_issue_requisite_bank_code_map_override():
    adapter = SafewayAdapter()
    p = _provider({"bank_code_map": {"sber": "custom_sber_gw"}})

    def responder(req: _FakeRequest):
        assert json.loads(req.read())["payment_gateway"] == "custom_sber_gw"
        return _FakeResponse(200, _ok(_H2H_DATA))

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={"amount": Decimal("6030"), "payment_method": PaymentMethod.CARD,
                        "payment_option_code": "sber"},
            idempotency_key="idem-4", timeout_ms=5000,
        )
    assert isinstance(result, ProviderRequisiteResponse)


@pytest.mark.asyncio
async def test_issue_requisite_success_false_is_refusal():
    adapter = SafewayAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(200, {"success": False, "message": "no available traders"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={"amount": Decimal("6030"), "payment_method": PaymentMethod.CARD,
                        "payment_option_code": "sber"},
            idempotency_key="idem-5", timeout_ms=5000,
        )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "no_capacity"
    assert "no available traders" in result.message


@pytest.mark.asyncio
async def test_issue_requisite_no_requisite_is_refusal():
    adapter = SafewayAdapter()
    p = _provider()

    data = {**_H2H_DATA, "sub_status": "waiting_details_to_be_selected",
            "payment_detail": {"detail": "", "detail_type": "card", "initials": ""}}

    def responder(_):
        return _FakeResponse(200, _ok(data))

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={"amount": Decimal("6030"), "payment_method": PaymentMethod.CARD,
                        "payment_option_code": "sber"},
            idempotency_key="idem-6", timeout_ms=5000,
        )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "no_capacity"


@pytest.mark.asyncio
async def test_issue_requisite_http_400_becomes_refusal():
    adapter = SafewayAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(400, {"success": False, "message": "bad request"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={"amount": Decimal("6030"), "payment_method": PaymentMethod.CARD,
                        "payment_option_code": "sber"},
            idempotency_key="idem-7", timeout_ms=5000,
        )
    assert isinstance(result, ProviderRefusal)


@pytest.mark.asyncio
async def test_issue_requisite_missing_merchant_id_is_refusal():
    """validate_provider_config refuses before any HTTP call."""
    adapter = SafewayAdapter()
    p = _provider({"merchant_id": ""})
    result = await adapter.issue_requisite(
        provider=p,
        order_data={"amount": Decimal("6030"), "payment_method": PaymentMethod.CARD,
                    "payment_option_code": "sber"},
        idempotency_key="idem-8", timeout_ms=5000,
    )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "misconfigured"


@pytest.mark.parametrize(
    "gateway_name,expected_code",
    [
        ("Сбербанк", "sber"),
        ("Т-Банк", "tbank"),            # provider-specific hyphen spelling
        ("ДОМ.РФ", "domrfbank"),        # not in the shared default alias map
        ("Росбанк", "rosbank"),         # not in the shared default
        ("Банк Санкт-Петербург", "bspb"),
        ("Ак Барс Банк", "akbars"),
        ("ОТП", "otp"),
    ],
)
@pytest.mark.asyncio
async def test_issue_requisite_resolves_bank_name_to_option_code(gateway_name, expected_code):
    adapter = SafewayAdapter()
    p = _provider()
    data = {**_H2H_DATA, "payment_gateway_name": gateway_name}

    def responder(_):
        return _FakeResponse(200, _ok(data))

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={"amount": Decimal("6030"), "payment_method": PaymentMethod.CARD,
                        "payment_option_code": None},
            idempotency_key="idem-bank", timeout_ms=5000,
        )
    assert isinstance(result, ProviderRequisiteResponse)
    assert result.payment_option_code == expected_code


# ─── cancel_request ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_request_200():
    adapter = SafewayAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "PATCH"
        assert req.url.endswith("/api/h2h/order/ord-1/cancel")
        return _FakeResponse(200, {"success": True})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.cancel_request(provider=p, external_order_id="ord-1", timeout_ms=2000)
    assert ok is True


@pytest.mark.asyncio
async def test_cancel_request_404_treated_as_success():
    adapter = SafewayAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(404, {"message": "not found"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.cancel_request(provider=p, external_order_id="GONE", timeout_ms=2000)
    assert ok is True


# ─── notify_receipt → non-destructive receipt endpoint ──────


@pytest.mark.asyncio
async def test_notify_receipt_posts_to_receipt_endpoint_non_destructive():
    """The receipt is forwarded to POST .../{external_id}/receipt (NOT the
    dispute endpoint), so the order keeps living. external_id is first resolved
    via GET .../{order_id}, then the base64 image + receipt_name are posted."""
    adapter = SafewayAdapter()
    p = _provider()

    raw_bytes = b"PNG receipt bytes"
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as fh:
        fh.write(raw_bytes)
        receipt_path = fh.name
    expected_b64 = base64.b64encode(raw_bytes).decode("ascii")
    seq = []

    def responder(req: _FakeRequest):
        if req.method == "GET":
            # Resolve OUR external_id from Safeway's order_id.
            assert req.url.endswith("/api/h2h/order/ord-1")
            seq.append("resolve")
            return _FakeResponse(200, _ok({"order_id": "ord-1", "external_id": "primepay-1"}))
        assert req.method == "POST"
        # Keyed by external_id, NOT order_id; and it's the receipt endpoint.
        assert req.url.endswith("/api/h2h/order/primepay-1/receipt")
        assert "/dispute" not in req.url
        seq.append("receipt")
        body = json.loads(req.read())
        # Safeway wants the raw base64 image — NOT a data URL.
        assert body["receipt"] == expected_b64
        assert not body["receipt"].startswith("data:")
        assert body["receipt_name"].endswith(".png")
        return _FakeResponse(200, _ok({"uuid": "ord-1", "external_id": "primepay-1", "timer_stopped": False}))

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.notify_receipt(
            provider=p, external_order_id="ord-1", receipt_path=receipt_path, comment="paid",
        )
    assert ok is True
    assert seq == ["resolve", "receipt"]


@pytest.mark.asyncio
async def test_notify_receipt_without_path_is_noop_success():
    """No receipt path = nothing to attach → no-op success (no HTTP call, and
    crucially no worker retry storm)."""
    adapter = SafewayAdapter()
    p = _provider()
    ok = await adapter.notify_receipt(
        provider=p, external_order_id="ord-1", receipt_path="", comment=None,
    )
    assert ok is True


@pytest.mark.asyncio
async def test_notify_receipt_http_error_returns_false():
    adapter = SafewayAdapter()
    p = _provider()
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as fh:
        fh.write(b"JPG")
        receipt_path = fh.name

    def responder(req: _FakeRequest):
        if req.method == "GET":
            return _FakeResponse(200, _ok({"order_id": "ord-1", "external_id": "primepay-1"}))
        return _FakeResponse(422, {"message": "receipt rejected"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.notify_receipt(
            provider=p, external_order_id="ord-1", receipt_path=receipt_path, comment=None,
        )
    assert ok is False


@pytest.mark.asyncio
async def test_notify_receipt_unresolvable_external_id_returns_false():
    """If the order can't be fetched (no external_id), we refuse rather than
    guess — and we must NOT fall back to the destructive dispute endpoint."""
    adapter = SafewayAdapter()
    p = _provider()
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as fh:
        fh.write(b"JPG")
        receipt_path = fh.name
    seq = []

    def responder(req: _FakeRequest):
        seq.append(req.method)
        assert "/dispute" not in req.url  # never escalate on a plain receipt
        return _FakeResponse(404, {"message": "not found"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.notify_receipt(
            provider=p, external_order_id="ord-1", receipt_path=receipt_path, comment=None,
        )
    assert ok is False
    assert seq == ["GET"]  # resolve failed → no POST attempted


# ─── raise_dispute → dispute endpoint (destructive escalation) ──────


@pytest.mark.asyncio
async def test_raise_dispute_posts_each_evidence():
    adapter = SafewayAdapter()
    p = _provider()
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as fh:
        fh.write(b"evidence")
        evidence_path = fh.name

    seq = []

    def responder(req: _FakeRequest):
        assert req.url.endswith("/api/h2h/order/ord-1/dispute")
        seq.append("dispute")
        assert "receipt" in json.loads(req.read())
        return _FakeResponse(200, {"success": True})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.raise_dispute(
            provider=p, external_order_id="ord-1", reason="paid but unconfirmed",
            evidence_paths=[evidence_path],
        )
    assert ok is True
    assert seq == ["dispute"]


@pytest.mark.asyncio
async def test_raise_dispute_no_evidence_refuses():
    adapter = SafewayAdapter()
    p = _provider()
    ok = await adapter.raise_dispute(
        provider=p, external_order_id="ord-1", reason="x", evidence_paths=[],
    )
    assert ok is False


# ─── parse_callback: unsigned, coarse-status-driven ────────────


def test_parse_callback_unsigned_success_with_amount():
    adapter = SafewayAdapter()
    p = _provider()
    data = {**_H2H_DATA, "status": "success", "sub_status": "successfully_paid"}
    body = json.dumps(data).encode()
    parsed = adapter.parse_callback(provider=p, headers={}, body=body)
    assert parsed.external_order_id == "ord-abc-123"
    assert parsed.status == ProviderStatus.SUCCESS
    assert parsed.paid_amount_fiat == Decimal("6030")


def test_parse_callback_accepts_data_wrapped_envelope():
    """Webhook may arrive as the bare order OR wrapped in {success,data}."""
    adapter = SafewayAdapter()
    p = _provider()
    body = json.dumps(_ok({**_H2H_DATA, "status": "success"})).encode()
    parsed = adapter.parse_callback(provider=p, headers={}, body=body)
    assert parsed.external_order_id == "ord-abc-123"
    assert parsed.status == ProviderStatus.SUCCESS


def test_parse_callback_ignores_sub_status():
    """sub_status is NOT read: a terminal-looking ``successfully_paid`` sub_status
    with a coarse ``status='pending'`` maps to PENDING (the coarse status wins)."""
    adapter = SafewayAdapter()
    p = _provider()
    body = json.dumps({"order_id": "o1", "status": "pending",
                       "sub_status": "successfully_paid"}).encode()
    parsed = adapter.parse_callback(provider=p, headers={}, body=body)
    assert parsed.status == ProviderStatus.PENDING


@pytest.mark.parametrize(
    "status,expected",
    [
        ("success", ProviderStatus.SUCCESS),
        ("pending", ProviderStatus.PENDING),
        ("fail", ProviderStatus.FAILED),
    ],
)
def test_parse_callback_maps_coarse_status(status, expected):
    """We map ONLY Safeway's coarse ``status`` field — sub_status is ignored."""
    adapter = SafewayAdapter()
    p = _provider()
    body = json.dumps({"order_id": "o1", "status": status}).encode()
    parsed = adapter.parse_callback(provider=p, headers={}, body=body)
    assert parsed.status == expected


def test_parse_callback_unknown_status_rejected():
    adapter = SafewayAdapter()
    p = _provider()
    body = json.dumps({"order_id": "o1", "status": "weird", "sub_status": "also_weird"}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers={}, body=body)


def test_parse_callback_missing_order_id_rejected():
    adapter = SafewayAdapter()
    p = _provider()
    body = json.dumps({"status": "success", "sub_status": "successfully_paid"}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers={}, body=body)


# ─── poll_status ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_status_happy_path():
    adapter = SafewayAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "GET"
        assert req.url.endswith("/api/h2h/order/ord-1")
        return _FakeResponse(200, _ok({**_H2H_DATA, "order_id": "ord-1",
                                       "status": "success"}))

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(provider=p, external_order_id="ord-1", timeout_ms=5000)
    assert parsed is not None
    assert parsed.external_order_id == "ord-1"
    assert parsed.status == ProviderStatus.SUCCESS


@pytest.mark.asyncio
async def test_poll_status_404_returns_none():
    adapter = SafewayAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(404, {"message": "not found"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(provider=p, external_order_id="ord-1", timeout_ms=5000)
    assert parsed is None


@pytest.mark.asyncio
async def test_poll_status_unknown_status_returns_none():
    adapter = SafewayAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(200, _ok({"order_id": "ord-1", "status": "weird"}))

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(provider=p, external_order_id="ord-1", timeout_ms=5000)
    assert parsed is None


# ─── get_balance ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_balance_reads_data_balance():
    adapter = SafewayAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        assert req.method == "GET"
        assert req.url.endswith("/api/wallet/balance")
        return _FakeResponse(200, _ok({"balance": "1234.56", "currency": "usdt"}))

    patcher, _ = _patch_httpx(responder)
    with patcher:
        balance = await adapter.get_balance(provider=p)
    assert balance == Decimal("1234.56")


@pytest.mark.asyncio
async def test_get_balance_non_200_returns_none():
    adapter = SafewayAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(500, {"message": "boom"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        balance = await adapter.get_balance(provider=p)
    assert balance is None


# ─── describe() ─────────────────────────────────────────────


def test_describe_marks_supported_methods_and_settings():
    adapter = SafewayAdapter()
    described = adapter.describe()
    assert set(m for m in described.supported_methods) >= {"sbp", "card"}
    keys = {f["key"] for f in described.settings_schema}
    assert {"merchant_id", "bank_code_map", "method_map"} <= keys
    assert any(f["key"] == "api_secret" for f in described.credentials_schema)
