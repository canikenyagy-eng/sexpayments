"""Unit tests for the Bitwire cascade adapter.

Bitwire exercises three patterns we hadn't tested before:
  * JWT acquisition + caching + refresh-on-expiry via the base's new
    async ``acquire_token`` hook.
  * Dual auth headers (Bearer + X-Api-Key) via ``EXTRA_AUTH_HEADER``.
  * GET-style callbacks (``GET ?id=...&status=...``) via the new
    ``query_params`` keyword on ``parse_callback``.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
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
from app.common.types import utcnow
from app.core.security import encrypt_api_secret
from app.modules.cascading.integrations.base import (
    CallbackVerificationError,
    ProviderRefusal,
    ProviderRequisiteResponse,
)
from app.modules.cascading.integrations.bitwire import BitwireAdapter
from tests.unit.cascade_adapter_testkit import (
    FakeRequest as _FakeRequest,
    FakeResponse as _FakeResponse,
    make_httpx_patch as _patch_httpx,
)


API_KEY = "store_api_key_12345"
PASSWORD = "merchant-password"
EMAIL = "merchant@example.com"
ACCOUNT_ID = "550e8400-e29b-41d4-a716-446655440000"
BASE_URL = "https://api.bitwire.finance"


def _provider(settings: Dict[str, Any] | None = None, *, with_creds: bool = True):
    p = MagicMock()
    p.id = 55
    p.code = "bitwire"
    p.adapter_type = "bitwire"
    p.base_url = BASE_URL
    p.api_secret_encrypted = encrypt_api_secret(PASSWORD) if with_creds else None
    p.api_key_encrypted = encrypt_api_secret(API_KEY) if with_creds else None
    p.webhook_secret_encrypted = None
    base_settings: Dict[str, Any] = {
        "email": EMAIL,
        "account_id": ACCOUNT_ID,
        "default_currency": "RUB",
        "callback_url": "https://primepay.example/api/cascade/v1/callbacks/bitwire",
        "jwt_refresh_skew_seconds": 60,
    }
    base_settings.update(settings or {})
    p.settings = base_settings
    p.request_timeout_ms = 5000
    p.cancel_timeout_ms = 2000
    return p


def _sign_in_response(expires_at: datetime, token: str = "JWT_TOKEN_1"):
    return _FakeResponse(
        200,
        {
            "token": token,
            "dateTimeExpires": expires_at.replace(tzinfo=timezone.utc).isoformat(),
        },
    )


_DEPOSIT_RESPONSE_SBP = {
    "amount": 1500.50,
    "amountByCurrency": 16.17305389,
    "currencyRate": 83.5000,
    "cardNumber": None,
    "issuer": "sberbank",
    "holderName": "Ivanov I A",
    "nspkCode": "100000000111",
    "orderId": "b7a02759-9ca5-4728-b542-dd471574ba1b",
    "phoneNumber": "+7(918)111-22-33",
    "timeExpires": "2025-08-01T12:27:04.610226149Z",
}

_DEPOSIT_RESPONSE_CARD = {
    "amount": 3000,
    "amountByCurrency": 32.53012048,
    "currencyRate": 83,
    "cardNumber": "5469 6700 2659 6588",
    "issuer": "sberbank",
    "holderName": "Ivanov I A",
    "nspkCode": "100000000111",
    "orderId": "a289fdc3-c2cb-4bf9-9061-1a3fd82dbd9b",
    "phoneNumber": None,
    "timeExpires": "2025-08-01T12:27:04.610226149Z",
}


# ─── Dual-header auth + JWT lifecycle ──────────────────────


def test_sign_request_emits_bearer_and_x_api_key():
    """``EXTRA_AUTH_HEADER`` knob auto-populates X-Api-Key from api_key."""
    adapter = BitwireAdapter()
    p = _provider()
    headers = adapter.sign_request(
        token="FAKE_JWT", method="POST", path="/x", body={}, provider=p
    )
    assert headers["Authorization"] == "Bearer FAKE_JWT"
    assert headers["X-Api-Key"] == API_KEY


@pytest.mark.asyncio
async def test_acquire_token_signs_in_then_caches():
    """First call sign-ins; second call hits the cache (no second HTTP)."""
    adapter = BitwireAdapter()
    p = _provider()
    later = utcnow() + timedelta(hours=1)
    calls = []

    def responder(req: _FakeRequest):
        calls.append((req.method, req.url))
        if req.url.endswith("/api/auth/sign-in"):
            body = json.loads(req.read())
            assert body["email"] == EMAIL
            assert body["password"] == PASSWORD
            return _sign_in_response(later)
        raise AssertionError(f"unexpected url {req.url}")

    patcher, _ = _patch_httpx(responder)
    with patcher:
        token1 = await adapter.acquire_token(p)
        token2 = await adapter.acquire_token(p)
    assert token1 == "JWT_TOKEN_1"
    assert token2 == "JWT_TOKEN_1"
    # Exactly one sign-in call regardless of how many acquire_token calls.
    assert len([c for c in calls if c[1].endswith("/sign-in")]) == 1


@pytest.mark.asyncio
async def test_acquire_token_re_signs_when_expired():
    adapter = BitwireAdapter()
    p = _provider()
    # Seed cache with an already-expired token.
    adapter._jwt_cache[p.id] = ("OLD_JWT", utcnow() - timedelta(minutes=5))
    later = utcnow() + timedelta(hours=1)

    def responder(req: _FakeRequest):
        assert req.url.endswith("/api/auth/sign-in")
        return _sign_in_response(later, token="NEW_JWT")

    patcher, _ = _patch_httpx(responder)
    with patcher:
        token = await adapter.acquire_token(p)
    assert token == "NEW_JWT"


@pytest.mark.asyncio
async def test_acquire_token_re_signs_within_refresh_skew():
    """If JWT expires within ``jwt_refresh_skew_seconds`` we refresh early."""
    adapter = BitwireAdapter()
    p = _provider({"jwt_refresh_skew_seconds": 600})  # 10-minute skew
    # Token expires in 30 seconds — inside the skew, must refresh.
    adapter._jwt_cache[p.id] = ("STALE", utcnow() + timedelta(seconds=30))
    later = utcnow() + timedelta(hours=1)

    def responder(_):
        return _sign_in_response(later, token="REFRESHED")

    patcher, _ = _patch_httpx(responder)
    with patcher:
        token = await adapter.acquire_token(p)
    assert token == "REFRESHED"


@pytest.mark.asyncio
async def test_acquire_token_signin_failure_raises():
    adapter = BitwireAdapter()
    p = _provider()

    def responder(_):
        return _FakeResponse(401, {"message": "Invalid credentials"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        with pytest.raises(CallbackVerificationError):
            await adapter.acquire_token(p)


# ─── supports ────────────────────────────────────────────────


def test_supports_sbp_card_only():
    adapter = BitwireAdapter()
    p = _provider()
    assert adapter.supports(provider=p, method=PaymentMethod.SBP, payment_option_code=None)
    assert adapter.supports(provider=p, method=PaymentMethod.CARD, payment_option_code=None)
    # SIM isn't in SUPPORTED_METHODS.
    assert not adapter.supports(provider=p, method=PaymentMethod.SIM, payment_option_code=None)


def test_supports_requires_account_id_setting():
    adapter = BitwireAdapter()
    p = _provider({"account_id": None})
    assert not adapter.supports(
        provider=p, method=PaymentMethod.CARD, payment_option_code=None
    )


# ─── issue_requisite ────────────────────────────────────────


@pytest.mark.asyncio
async def test_issue_requisite_sbp_happy_path():
    adapter = BitwireAdapter()
    p = _provider()
    later = utcnow() + timedelta(hours=1)

    def responder(req: _FakeRequest):
        if req.url.endswith("/api/auth/sign-in"):
            return _sign_in_response(later)
        assert req.method == "POST"
        assert req.url.endswith(f"/api/merchant/order/{ACCOUNT_ID}/deposit")
        # Dual auth headers!
        assert req.headers["Authorization"] == "Bearer JWT_TOKEN_1"
        assert req.headers["X-Api-Key"] == API_KEY
        body = json.loads(req.read())
        assert body["isSbp"] is True
        assert body["amount"] == "1500.50"  # format_amount preserves Decimal precision
        assert body["currency"] == "RUB"
        assert body["internalId"] == "primepay-1"
        assert body["issuer"] == "sberbank"
        return _FakeResponse(200, _DEPOSIT_RESPONSE_SBP)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("1500.50"),
                "payment_method": PaymentMethod.SBP,
                "payment_option_code": "sberbank",
                "merchant_request_id": "primepay-1",
            },
            idempotency_key="idem-1",
            timeout_ms=5000,
        )

    assert isinstance(result, ProviderRequisiteResponse)
    assert result.external_order_id == _DEPOSIT_RESPONSE_SBP["orderId"]
    assert result.account_number == "+7(918)111-22-33"  # phoneNumber wins for SBP
    assert result.payment_method == PaymentMethod.SBP
    assert result.bank_name == "sberbank"
    assert result.account_holder == "Ivanov I A"
    assert result.amount_fiat == Decimal("1500.50")
    assert result.provider_rate == Decimal("83.5000")
    # expires_at was parsed from timeExpires.
    assert result.expires_at.year == 2025


@pytest.mark.asyncio
async def test_issue_requisite_card_uses_cardnumber():
    adapter = BitwireAdapter()
    p = _provider()
    later = utcnow() + timedelta(hours=1)

    def responder(req: _FakeRequest):
        if req.url.endswith("/api/auth/sign-in"):
            return _sign_in_response(later)
        body = json.loads(req.read())
        assert body["isSbp"] is False
        return _FakeResponse(200, _DEPOSIT_RESPONSE_CARD)

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("3000"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": None,
            },
            idempotency_key="idem-c",
            timeout_ms=5000,
        )

    assert isinstance(result, ProviderRequisiteResponse)
    assert result.account_number == "5469 6700 2659 6588"  # cardNumber for card
    assert result.payment_method == PaymentMethod.CARD


@pytest.mark.asyncio
async def test_issue_requisite_404_returns_no_capacity():
    adapter = BitwireAdapter()
    p = _provider()
    later = utcnow() + timedelta(hours=1)

    def responder(req: _FakeRequest):
        if req.url.endswith("/api/auth/sign-in"):
            return _sign_in_response(later)
        return _FakeResponse(404, {"message": "Реквизиты не найдены"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        result = await adapter.issue_requisite(
            provider=p,
            order_data={
                "amount": Decimal("1800"),
                "payment_method": PaymentMethod.CARD,
                "payment_option_code": None,
            },
            idempotency_key="x",
            timeout_ms=5000,
        )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "no_capacity"


@pytest.mark.asyncio
async def test_issue_requisite_missing_account_id_misconfigured():
    adapter = BitwireAdapter()
    p = _provider({"account_id": None})
    # No HTTP patch — must short-circuit before any call.
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


# ─── cancel_request ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_request_2xx_succeeds():
    adapter = BitwireAdapter()
    p = _provider()
    later = utcnow() + timedelta(hours=1)
    # Pre-seed JWT cache to avoid the sign-in detour during this assertion.
    adapter._jwt_cache[p.id] = ("CACHED_JWT", later)

    def responder(req: _FakeRequest):
        assert req.method == "POST"
        assert req.url.endswith("/api/merchant/order/TRADE-1/cancel")
        assert req.headers["Authorization"] == "Bearer CACHED_JWT"
        return _FakeResponse(200, {"message": "Success!"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.cancel_request(
            provider=p, external_order_id="TRADE-1", timeout_ms=2000
        )
    assert ok is True


@pytest.mark.asyncio
async def test_cancel_request_404_still_succeeds():
    adapter = BitwireAdapter()
    p = _provider()
    adapter._jwt_cache[p.id] = ("JWT", utcnow() + timedelta(hours=1))

    def responder(_):
        return _FakeResponse(404, {"message": "not found"})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        ok = await adapter.cancel_request(
            provider=p, external_order_id="GONE", timeout_ms=2000
        )
    assert ok is True


# ─── notify_receipt / raise_dispute are no-ops ──────────────


@pytest.mark.asyncio
async def test_notify_receipt_is_noop():
    adapter = BitwireAdapter()
    p = _provider()
    # No HTTP patch.
    ok = await adapter.notify_receipt(
        provider=p,
        external_order_id="T",
        receipt_path="/whatever.jpg",
        comment=None,
    )
    assert ok is True


@pytest.mark.asyncio
async def test_raise_dispute_is_noop():
    adapter = BitwireAdapter()
    p = _provider()
    ok = await adapter.raise_dispute(
        provider=p,
        external_order_id="T",
        reason="x",
        evidence_paths=[],
    )
    assert ok is True


# ─── parse_callback uses query_params (GET-style) ───────────


def test_parse_callback_basic_completed():
    adapter = BitwireAdapter()
    p = _provider()
    parsed = adapter.parse_callback(
        provider=p,
        headers={},
        body=b"",
        query_params={
            "id": "order_67890",
            "status": "COMPLETED",
        },
    )
    assert parsed.external_order_id == "order_67890"
    assert parsed.status == ProviderStatus.SUCCESS
    assert parsed.paid_amount_fiat is None  # no reconciliation = no amount


def test_parse_callback_with_reconciliation_fields():
    adapter = BitwireAdapter()
    p = _provider()
    parsed = adapter.parse_callback(
        provider=p,
        headers={},
        body=b"",
        query_params={
            "id": "order_67890",
            "status": "COMPLETED",
            "reconciliationSum": "100.00",
            "reconciliationAmount": "10000.00",
            "reconciliationRate": "100.00",
        },
    )
    assert parsed.status == ProviderStatus.SUCCESS
    assert parsed.paid_amount_fiat == Decimal("10000.00")
    assert parsed.raw["reconciliationSum"] == "100.00"
    assert parsed.raw["reconciliationRate"] == "100.00"


def test_parse_callback_all_states():
    adapter = BitwireAdapter()
    p = _provider()
    cases = {
        "PENDING": ProviderStatus.PENDING,
        "COMPLETED": ProviderStatus.SUCCESS,
        "CANCELED": ProviderStatus.CANCELED,
        "DISPUTE": ProviderStatus.DISPUTED,
    }
    for raw, expected in cases.items():
        parsed = adapter.parse_callback(
            provider=p,
            headers={},
            body=b"",
            query_params={"id": "X", "status": raw},
        )
        assert parsed.status == expected, raw


def test_parse_callback_missing_id_rejected():
    adapter = BitwireAdapter()
    p = _provider()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(
            provider=p,
            headers={},
            body=b"",
            query_params={"status": "COMPLETED"},
        )


def test_parse_callback_unknown_status_rejected():
    adapter = BitwireAdapter()
    p = _provider()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(
            provider=p,
            headers={},
            body=b"",
            query_params={"id": "X", "status": "weird"},
        )


# ─── get_balance ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_balance_returns_amount():
    adapter = BitwireAdapter()
    p = _provider()
    adapter._jwt_cache[p.id] = ("JWT", utcnow() + timedelta(hours=1))

    def responder(req: _FakeRequest):
        assert req.method == "GET"
        assert req.url.endswith("/api/merchant/balance")
        assert req.headers["Authorization"] == "Bearer JWT"
        assert req.headers["X-Api-Key"] == API_KEY
        return _FakeResponse(
            200, {"balance": "123.4567", "currency": "RUB", "name": "Store 1"}
        )

    patcher, _ = _patch_httpx(responder)
    with patcher:
        balance = await adapter.get_balance(provider=p)
    assert balance == Decimal("123.4567")


@pytest.mark.asyncio
async def test_get_balance_non_200_returns_none():
    adapter = BitwireAdapter()
    p = _provider()
    adapter._jwt_cache[p.id] = ("JWT", utcnow() + timedelta(hours=1))

    def responder(_):
        return _FakeResponse(503, {})

    patcher, _ = _patch_httpx(responder)
    with patcher:
        balance = await adapter.get_balance(provider=p)
    assert balance is None


# ─── describe() ──────────────────────────────────────────────


def test_describe_lists_settings_keys():
    info = BitwireAdapter.describe()
    keys = [f["key"] for f in info.settings_schema]
    assert "email" in keys
    assert "account_id" in keys
    assert "bank_code_map" in keys


def test_describe_supports_provider_rate():
    info = BitwireAdapter.describe()
    assert info.supports_provider_rate is True
    assert set(info.supported_methods) == {"sbp", "card"}
