"""Unit tests for the LegacyCrypto cascade adapter.

We mock ``httpx.AsyncClient`` so tests don't hit the network. A small fake
client records the request the adapter sent (so we can assert headers,
signature, body) and returns a canned response.

Coverage:
  * supports() — Card/SBP yes, SIM no, custom map override
  * issue_requisite — happy path (signed POST, parsed reply), no-capacity refusal,
    HTTP 4xx → typed refusal, unsupported method → short-circuit
  * cancel_request — 200 / 404 both succeed, 5xx fails
  * parse_callback — signature validated, payin/payout events parsed,
    bad signature / unknown event / unknown status all rejected
  * HMAC signature is exactly hex(HMAC_SHA256(token, "{ts}.{METHOD}.{path}{canonical_json}"))
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional
from unittest.mock import MagicMock, patch

# Pydantic-settings reads env eagerly at import time.
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
from app.modules.cascading.integrations.legacy_crypto import LegacyCryptoAdapter
from tests.unit.cascade_adapter_testkit import (
    FakeRequest as _FakeRequest,
    FakeResponse as _FakeResponse,
    make_httpx_patch as _patch_httpx,
)

# Use the base-class signing primitives through a default-constructed adapter —
# this is how production code calls them too (self.canonical_json / self.compute_signature).
_adapter_for_signing = LegacyCryptoAdapter()


def _canonical_json(body):
    return _adapter_for_signing.canonical_json(body)


def _sign(*, token: str, timestamp: str, method: str, path: str, body_json: str) -> str:
    payload = _adapter_for_signing.build_signature_payload(
        timestamp=timestamp, method=method, path=path, body_json=body_json
    )
    return _adapter_for_signing.compute_signature(secret=token, payload=payload)


TEST_TOKEN = "secret-token-abc"
TEST_WEBHOOK_SECRET = "secret-webhook"
BASE_URL = "https://payment-legacy.com"


def _provider(settings: Dict[str, Any] | None = None, *, with_webhook: bool = True):
    p = MagicMock()
    p.id = 7
    p.code = "legacy_crypto"
    p.adapter_type = "legacy_crypto"
    p.base_url = BASE_URL
    p.api_secret_encrypted = encrypt_api_secret(TEST_TOKEN)
    p.api_key_encrypted = None
    p.webhook_secret_encrypted = (
        encrypt_api_secret(TEST_WEBHOOK_SECRET) if with_webhook else None
    )
    p.settings = settings or {}
    p.request_timeout_ms = 5000
    p.cancel_timeout_ms = 1000
    return p


# Fake httpx client / patch helpers are imported from cascade_adapter_testkit
# (see top of this file). The aliases _FakeRequest / _FakeResponse / _patch_httpx
# kept the existing test bodies unchanged.


# ─── HMAC signing ────────────────────────────────────────────


def test_canonical_json_empty_body():
    assert _canonical_json(None) == "{}"
    assert _canonical_json({}) == "{}"


def test_canonical_json_sorts_keys():
    body = {"b": 2, "a": 1, "c": [3, 1, 2]}
    assert _canonical_json(body) == '{"a":1,"b":2,"c":[3,1,2]}'


def test_sign_format_matches_spec():
    sig = _sign(
        token="tok",
        timestamp="1739532000",
        method="POST",
        path="/x",
        body_json='{"a":1}',
    )
    expected = hmac.new(
        b"tok", b'1739532000.POST./x{"a":1}', hashlib.sha256
    ).hexdigest()
    assert sig == expected


# ─── supports() ──────────────────────────────────────────────


def test_supports_card_and_sbp_by_default():
    adapter = LegacyCryptoAdapter()
    assert adapter.supports(provider=_provider(), method=PaymentMethod.CARD, payment_option_code=None)
    assert adapter.supports(provider=_provider(), method=PaymentMethod.SBP, payment_option_code=None)


def test_supports_rejects_sim():
    adapter = LegacyCryptoAdapter()
    assert not adapter.supports(
        provider=_provider(), method=PaymentMethod.SIM, payment_option_code=None
    )


def test_supports_with_custom_method_map():
    adapter = LegacyCryptoAdapter()
    p = _provider({"preferred_method_map": {"sbp": "SBP"}})
    assert adapter.supports(provider=p, method=PaymentMethod.SBP, payment_option_code=None)
    assert not adapter.supports(provider=p, method=PaymentMethod.CARD, payment_option_code=None)


# ─── issue_requisite ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_issue_requisite_happy_path():
    adapter = LegacyCryptoAdapter()
    p = _provider()

    successful_response = {
        "success": True,
        "data": {
            "public_id": "PI-abc123xyz",
            "status": "Working",
            "usdt_amount": "98.12345678",
            "rub_amount": "10000.00",
            "rate_with_commission": "101.90",
            "info": "test",
            "linked_requests": [
                {
                    "matching_id": 1842,
                    "matching_status": "Working",
                    "amount_matching_rub": "10000.00",
                    "payout_requisites": "5555666677778888",
                    "payout_additional_requisites": "",
                    "payout_bank": "VTB",
                    "payout_preferred_method": "Card",
                    "payout_full_name": "Ivan Ivanov",
                }
            ],
        },
    }

    def responder(req: _FakeRequest) -> _FakeResponse:
        return _FakeResponse(201, successful_response)

    patcher, captured = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("10000"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": "sber",
                "client_full_name": "Ivan Ivanov",
                "merchant_request_id": "OUR-REQ-001",
            },
            idempotency_key="idem-key-1",
            timeout_ms=5000,
        )

    assert isinstance(result, ProviderRequisiteResponse)
    assert result.external_order_id == "PI-abc123xyz"
    assert result.bank_name == "VTB"
    assert result.account_number == "5555666677778888"
    assert result.account_holder == "Ivan Ivanov"
    assert result.payment_method == PaymentMethod.CARD
    assert result.amount_fiat == Decimal("10000.00")
    assert result.provider_rate == Decimal("101.90")

    request = captured[0].calls[-1]
    assert request.headers["Authorization"] == f"Bearer {TEST_TOKEN}"
    assert request.headers["X-Idempotency-Key"] == "idem-key-1"
    timestamp = request.headers["X-Timestamp"]
    body_json = request.read().decode()
    body = json.loads(body_json)
    canonical = _canonical_json(body)
    expected_signature = _sign(
        token=TEST_TOKEN,
        timestamp=timestamp,
        method="POST",
        path="/api/v1/merchant/requests/simple/",
        body_json=canonical,
    )
    assert request.headers["X-Signature"] == expected_signature
    assert body["client_bank"] == "SBER"
    assert body["preferred_method"] == "Card"
    assert body["sub_direction"] == "payin_simple"
    assert body["client_amount"] == "10000"
    assert body["callback_url"] == "https://api.test/api/cascade/v1/callbacks/legacy_crypto"


@pytest.mark.asyncio
async def test_issue_requisite_no_linked_returns_refusal():
    adapter = LegacyCryptoAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(
            201,
            {"success": True, "data": {"public_id": "PI-x", "status": "Working", "linked_requests": []}},
        )

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("100"),
                "payment_method": PaymentMethod.SBP,
                "payment_option_code": None,
            },
            idempotency_key="k",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "no_capacity"


@pytest.mark.asyncio
@pytest.mark.parametrize("status_raw", ["Canceled", "Confirmed", "Waiting", "Processing"])
async def test_issue_requisite_non_working_status_refuses_even_with_linked(status_raw):
    """Only ``Working`` yields a real requisite. Every other status — terminal
    (Canceled/Confirmed/Waiting) OR not-yet-linked (Processing) — must refuse even
    when the response still carries linked_requests + public_id, otherwise the
    cascade would win the race with a dead/absent requisite."""
    adapter = LegacyCryptoAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(201, {
            "success": True,
            "data": {
                "public_id": "PI-dead",
                "status": status_raw,
                "rub_amount": "10000.00",
                "rate_with_commission": "101.90",
                "linked_requests": [{
                    "matching_status": status_raw,
                    "payout_requisites": "5555666677778888",
                    "payout_bank": "VTB",
                    "payout_preferred_method": "Card",
                    "payout_full_name": "Ivan Ivanov",
                }],
            },
        })

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("10000"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": "sber",
            },
            idempotency_key="k-dead",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRefusal), f"{status_raw} must refuse, not win"
    assert result.code == "no_capacity"


@pytest.mark.asyncio
async def test_issue_requisite_working_status_wins():
    """``Working`` is the one live status — a real linked requisite → WON. Mirrors
    the exact prod response shape (status=Working + linked_requests[0].payout_*)."""
    adapter = LegacyCryptoAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(201, {
            "success": True,
            "data": {
                "public_id": "PI-KNJj7fJu2I3M",
                "status": "Working",
                "rub_amount": "4154.00",
                "rate_with_commission": "87.38100000",
                "linked_requests": [{
                    "matching_id": 35477,
                    "matching_status": "Working",
                    "payout_requisites": "2203830251197555",
                    "payout_bank": "МТС Банк",
                    "payout_preferred_method": "Card",
                    "payout_full_name": "",
                }],
            },
        })

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("4154"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": "mtsbank",
            },
            idempotency_key="k-working",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRequisiteResponse)
    assert result.external_order_id == "PI-KNJj7fJu2I3M"
    assert result.account_number == "2203830251197555"


def test_response_business_error_flags_non_working_payin():
    """Provider-request log: a non-live payin (Canceled / Confirmed / …) records as
    an ERROR, ``Working`` stays OK, and non-payin request types + garbage bodies are
    never downgraded."""
    a = LegacyCryptoAdapter()

    def body(status):
        return json.dumps({"success": True, "data": {
            "public_id": "PI", "status": status,
            "linked_requests": [{"payout_requisites": "2203830251197555"}],
        }})

    # non-live payin 2xx → error reason (not OK)
    assert a._response_business_error(request_type="payin", status=201, response_text=body("Canceled"))
    assert a._response_business_error(request_type="payin", status=201, response_text=body("Confirmed"))
    # the one live status stays OK
    assert a._response_business_error(request_type="payin", status=201, response_text=body("Working")) is None
    # cancel/poll/balance calls are never downgraded on a Canceled-looking body
    assert a._response_business_error(request_type="cancel", status=201, response_text=body("Canceled")) is None
    # unparseable body → no crash, no downgrade
    assert a._response_business_error(request_type="payin", status=201, response_text="not json") is None


@pytest.mark.asyncio
async def test_issue_requisite_4xx_returns_refusal():
    adapter = LegacyCryptoAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(403, {"success": False, "error": "IP denied"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("100"),
                "payment_method": PaymentMethod.SBP,
                "payment_option_code": None,
            },
            idempotency_key="k",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "forbidden"


@pytest.mark.asyncio
async def test_issue_requisite_unsupported_method_short_circuits():
    adapter = LegacyCryptoAdapter()
    p = _provider()
    # No HTTP patcher — this path must not make a network call at all.
    result = await adapter.issue_requisite(
        provider=p,
        order_data={
            "amount": Decimal("100"),
            "payment_method": PaymentMethod.SIM,
            "payment_option_code": None,
        },
        idempotency_key="k",
        timeout_ms=5000,
    )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "unsupported_method"


# ─── default body fields (mask requisites, bank fallback, callback url) ─


def _capture_body(adapter, provider, order_data, method, idempotency_key="idem"):
    """Build the payin request via the adapter and return the body dict."""
    method_value = adapter.resolve_method_value(provider, method)
    req = adapter.build_payin_request(
        provider=provider,
        order_data=order_data,
        idempotency_key=idempotency_key,
        method=method,
        method_value=method_value,
    )
    return req.body


def test_client_requisites_defaults_to_sbp_phone_when_missing():
    body = _capture_body(
        LegacyCryptoAdapter(),
        _provider(),
        {"amount": Decimal("100"), "payment_method": PaymentMethod.SBP, "payment_option_code": None},
        PaymentMethod.SBP,
    )
    assert body["client_requisites"] == LegacyCryptoAdapter._DEFAULT_REQUISITES_SBP


def test_client_requisites_defaults_to_card_pan_when_missing():
    body = _capture_body(
        LegacyCryptoAdapter(),
        _provider(),
        {"amount": Decimal("100"), "payment_method": PaymentMethod.CARD, "payment_option_code": None},
        PaymentMethod.CARD,
    )
    assert body["client_requisites"] == LegacyCryptoAdapter._DEFAULT_REQUISITES_CARD


def test_client_requisites_provided_value_wins_over_mask():
    body = _capture_body(
        LegacyCryptoAdapter(),
        _provider(),
        {
            "amount": Decimal("100"),
            "payment_method": PaymentMethod.SBP,
            "payment_option_code": None,
            "client_requisites": "79991234567",
        },
        PaymentMethod.SBP,
    )
    assert body["client_requisites"] == "79991234567"


def test_client_requisites_settings_override_mask():
    p = _provider(
        {
            "default_client_requisites_sbp": "70000000001",
            "default_client_requisites_card": "4111111111111111",
        }
    )
    sbp_body = _capture_body(
        LegacyCryptoAdapter(),
        p,
        {"amount": Decimal("100"), "payment_method": PaymentMethod.SBP, "payment_option_code": None},
        PaymentMethod.SBP,
    )
    card_body = _capture_body(
        LegacyCryptoAdapter(),
        p,
        {"amount": Decimal("100"), "payment_method": PaymentMethod.CARD, "payment_option_code": None},
        PaymentMethod.CARD,
    )
    assert sbp_body["client_requisites"] == "70000000001"
    assert card_body["client_requisites"] == "4111111111111111"


def test_client_bank_fallback_from_settings_when_no_option_code():
    p = _provider({"default_client_bank": "SBER"})
    body = _capture_body(
        LegacyCryptoAdapter(),
        p,
        {"amount": Decimal("100"), "payment_method": PaymentMethod.SBP, "payment_option_code": None},
        PaymentMethod.SBP,
    )
    assert body["client_bank"] == "SBER"


def test_client_bank_always_uses_placeholder_when_no_option_code_no_settings():
    body = _capture_body(
        LegacyCryptoAdapter(),
        _provider(),
        {"amount": Decimal("100"), "payment_method": PaymentMethod.SBP, "payment_option_code": None},
        PaymentMethod.SBP,
    )
    # Without payment_option_code or default_client_bank setting the
    # adapter still sends a non-empty client_bank so the provider's
    # "client_bank is required" doesn't fire.
    assert body["client_bank"] == LegacyCryptoAdapter._DEFAULT_BANK_PLACEHOLDER


def test_option_code_takes_precedence_over_default_bank():
    p = _provider({"default_client_bank": "VTB"})
    body = _capture_body(
        LegacyCryptoAdapter(),
        p,
        {
            "amount": Decimal("100"),
            "payment_method": PaymentMethod.SBP,
            "payment_option_code": "sber",
        },
        PaymentMethod.SBP,
    )
    # bank_code_map default = upper(option_code) → "SBER" beats VTB default.
    assert body["client_bank"] == "SBER"


def test_callback_url_auto_formed_per_provider_code():
    adapter = LegacyCryptoAdapter()
    # Two providers with the same adapter_type but different codes — each
    # gets its own URL, derived from CascadeProvider.code (not adapter type).
    main = _provider()
    main.code = "legacy_main"
    backup = _provider()
    backup.code = "legacy_backup"

    body_main = _capture_body(
        adapter,
        main,
        {"amount": Decimal("100"), "payment_method": PaymentMethod.SBP, "payment_option_code": None},
        PaymentMethod.SBP,
    )
    body_backup = _capture_body(
        adapter,
        backup,
        {"amount": Decimal("100"), "payment_method": PaymentMethod.SBP, "payment_option_code": None},
        PaymentMethod.SBP,
    )
    assert body_main["callback_url"] == "https://api.test/api/cascade/v1/callbacks/legacy_main"
    assert body_backup["callback_url"] == "https://api.test/api/cascade/v1/callbacks/legacy_backup"


# ─── cancel_request ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_request_200():
    adapter = LegacyCryptoAdapter()
    p = _provider()

    def responder(req: _FakeRequest):
        body = json.loads(req.read())
        assert body == {"public_id": "PI-1"}
        return _FakeResponse(200, {"success": True, "data": {}})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.cancel_request(
            provider=p, external_order_id="PI-1", timeout_ms=2000
        )
    assert ok is True


@pytest.mark.asyncio
async def test_cancel_request_404_treated_as_success():
    adapter = LegacyCryptoAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(404, {"success": False, "error": "Payin not found"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.cancel_request(
            provider=p, external_order_id="PI-x", timeout_ms=2000
        )
    assert ok is True


@pytest.mark.asyncio
async def test_cancel_request_500_fails():
    adapter = LegacyCryptoAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(500)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.cancel_request(
            provider=p, external_order_id="PI-x", timeout_ms=2000
        )
    assert ok is False


# ─── parse_callback ─────────────────────────────────────────


def _signed_webhook(body: Dict[str, Any]) -> tuple[Dict[str, str], bytes]:
    body_bytes = json.dumps(body).encode("utf-8")
    sig = hmac.new(TEST_WEBHOOK_SECRET.encode(), body_bytes, hashlib.sha256).hexdigest()
    return {"X-Signature": sig}, body_bytes


def test_parse_callback_payin_status_changed():
    adapter = LegacyCryptoAdapter()
    p = _provider()
    headers, body = _signed_webhook(
        {
            "event": "payin.status.changed",
            "timestamp": "2026-05-08T11:32:29.045Z",
            "data": {
                "merchant_id": "M-1",
                "payin_public_id": "PI-abc",
                "payin_status": "Confirmed",
                "payin_rub_amount": "10000.00",
            },
        }
    )
    parsed = adapter.parse_callback(provider=p, headers=headers, body=body)
    assert parsed.external_order_id == "PI-abc"
    assert parsed.status == ProviderStatus.SUCCESS


def test_parse_callback_payin_paid_status_carries_amount():
    adapter = LegacyCryptoAdapter()
    p = _provider()
    headers, body = _signed_webhook(
        {
            "event": "payin.status.changed",
            "data": {
                "payin_public_id": "PI-abc",
                "payin_status": "Waiting",
                "payin_rub_amount": "12345.67",
            },
        }
    )
    parsed = adapter.parse_callback(provider=p, headers=headers, body=body)
    assert parsed.status == ProviderStatus.PAID
    assert parsed.paid_amount_fiat == Decimal("12345.67")


def test_parse_callback_payout_event():
    adapter = LegacyCryptoAdapter()
    p = _provider()
    headers, body = _signed_webhook(
        {
            "event": "payout.status.changed",
            "data": {
                "payout_public_id": "PO-xyz",
                "payout_status": "Canceled",
            },
        }
    )
    parsed = adapter.parse_callback(provider=p, headers=headers, body=body)
    assert parsed.external_order_id == "PO-xyz"
    assert parsed.status == ProviderStatus.CANCELED


def test_parse_callback_invalid_signature_rejected():
    adapter = LegacyCryptoAdapter()
    p = _provider()
    body = json.dumps(
        {
            "event": "payin.status.changed",
            "data": {"payin_public_id": "PI-x", "payin_status": "Confirmed"},
        }
    ).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(
            provider=p, headers={"X-Signature": "bad"}, body=body
        )


def test_parse_callback_unsigned_accepted_even_with_secret():
    """The provider's MatchingWebhook delivers UNSIGNED callbacks. Even when a
    webhook secret IS configured, a callback with NO ``X-Signature`` header is
    accepted (inbound signature is optional) — only a PRESENT-but-wrong
    signature is rejected (see test_parse_callback_invalid_signature_rejected).
    Regression for the dev incident: secret set + unsigned webhook → 401."""
    adapter = LegacyCryptoAdapter()
    p = _provider()  # has a webhook secret configured
    body = json.dumps(
        {
            "event": "payin.status.changed",
            "data": {"payin_public_id": "PI-unsigned", "payin_status": "Confirmed"},
        }
    ).encode()
    parsed = adapter.parse_callback(provider=p, headers={}, body=body)
    assert parsed.external_order_id == "PI-unsigned"
    assert parsed.status == ProviderStatus.SUCCESS


def test_parse_callback_no_secret_skips_signature_check():
    adapter = LegacyCryptoAdapter()
    p = _provider(with_webhook=False)
    body = json.dumps(
        {
            "event": "payin.status.changed",
            "data": {"payin_public_id": "PI-x", "payin_status": "Working"},
        }
    ).encode()
    parsed = adapter.parse_callback(provider=p, headers={}, body=body)
    assert parsed.status == ProviderStatus.PENDING


def test_parse_callback_unknown_status_rejected():
    adapter = LegacyCryptoAdapter()
    p = _provider()
    headers, body = _signed_webhook(
        {
            "event": "payin.status.changed",
            "data": {"payin_public_id": "PI-x", "payin_status": "MadeUp"},
        }
    )
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers=headers, body=body)


def test_parse_callback_unknown_event_rejected():
    adapter = LegacyCryptoAdapter()
    p = _provider()
    headers, body = _signed_webhook(
        {"event": "garbage.event", "data": {"payin_public_id": "PI-x", "payin_status": "Working"}}
    )
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers=headers, body=body)


# ─── poll_status ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_poll_status_returns_parsed_callback():
    adapter = LegacyCryptoAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(
            200,
            {
                "success": True,
                "data": {
                    "public_id": "PI-abc",
                    "status": "Confirmed",
                    "usdt_amount": "10",
                    "rub_amount": "1000",
                    "rate_with_commission": "100",
                    "info": "",
                    "linked_requests": [],
                },
            },
        )

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(
            provider=p, external_order_id="PI-abc", timeout_ms=2000
        )
    assert parsed is not None
    assert parsed.status == ProviderStatus.SUCCESS


@pytest.mark.asyncio
async def test_poll_status_404_returns_none():
    adapter = LegacyCryptoAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(404, {"success": False, "error": "Payin not found"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        parsed = await adapter.poll_status(
            provider=p, external_order_id="PI-x", timeout_ms=2000
        )
    assert parsed is None
