"""Unit tests for the Mock provider adapter."""
from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from app.common.enums.cascading import ProviderStatus
from app.common.enums.payments import PaymentMethod
from app.modules.cascading.integrations.base import (
    CallbackVerificationError,
    ProviderRefusal,
    ProviderRequisiteResponse,
)
from app.modules.cascading.integrations.mock import MockProviderAdapter


def _provider(settings: dict | None = None, webhook_secret: str | None = None):
    p = MagicMock()
    p.id = 1
    p.code = "mock"
    p.adapter_type = "mock"
    p.settings = settings or {}
    p.webhook_secret_encrypted = None
    if webhook_secret:
        p.settings = {**p.settings, "webhook_secret": webhook_secret}
    p.cancel_timeout_ms = 1000
    p.request_timeout_ms = 1000
    return p


@pytest.mark.asyncio
async def test_supports_default_all_methods():
    adapter = MockProviderAdapter()
    assert adapter.supports(
        provider=_provider(), method=PaymentMethod.SBP, payment_option_code=None
    )


@pytest.mark.asyncio
async def test_supports_filtered_by_settings():
    adapter = MockProviderAdapter()
    p = _provider({"supported_methods": ["sbp"]})
    assert adapter.supports(provider=p, method=PaymentMethod.SBP, payment_option_code=None)
    assert not adapter.supports(
        provider=p, method=PaymentMethod.CARD, payment_option_code=None
    )


@pytest.mark.asyncio
async def test_issue_returns_requisite():
    adapter = MockProviderAdapter()
    p = _provider({"default_account": {"bank_name": "B", "account_number": "1", "account_holder": "H"}})
    result = await adapter.issue_requisite(
        provider=p,
        order_data={
            "amount": Decimal("1000"),
            "payment_method": PaymentMethod.SBP,
            "ttl_seconds": 600,
        },
        idempotency_key="abc123",
        timeout_ms=5000,
    )
    assert isinstance(result, ProviderRequisiteResponse)
    assert result.external_order_id == "mock-abc123"
    assert result.bank_name == "B"
    assert result.amount_fiat == Decimal("1000")


@pytest.mark.asyncio
async def test_issue_returns_refusal_when_configured():
    adapter = MockProviderAdapter()
    p = _provider({"refuse": True, "refuse_code": "no_capacity"})
    result = await adapter.issue_requisite(
        provider=p,
        order_data={"amount": 100, "payment_method": PaymentMethod.SBP},
        idempotency_key="x",
        timeout_ms=1000,
    )
    assert isinstance(result, ProviderRefusal)
    assert result.code == "no_capacity"


def test_parse_callback_validates_signature():
    adapter = MockProviderAdapter()
    secret = "shh-test-secret"
    p = _provider({"webhook_secret": secret})
    body = json.dumps({"external_order_id": "mock-1", "status": "paid"}).encode()
    valid_signature = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    parsed = adapter.parse_callback(
        provider=p,
        headers={"X-Mock-Signature": valid_signature},
        body=body,
    )
    assert parsed.external_order_id == "mock-1"
    assert parsed.status == ProviderStatus.PAID


def test_parse_callback_rejects_bad_signature():
    adapter = MockProviderAdapter()
    p = _provider({"webhook_secret": "right"})
    body = json.dumps({"external_order_id": "x", "status": "success"}).encode()

    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(
            provider=p,
            headers={"X-Mock-Signature": "wrong"},
            body=body,
        )


def test_parse_callback_no_secret_skips_signature_check():
    adapter = MockProviderAdapter()
    p = _provider({})  # no webhook_secret in settings, no encrypted secret
    body = json.dumps({"external_order_id": "y", "status": "paid"}).encode()
    parsed = adapter.parse_callback(provider=p, headers={}, body=body)
    assert parsed.external_order_id == "y"


def test_parse_callback_rejects_unknown_status():
    adapter = MockProviderAdapter()
    p = _provider({})
    body = json.dumps({"external_order_id": "z", "status": "totally_made_up"}).encode()
    with pytest.raises(CallbackVerificationError):
        adapter.parse_callback(provider=p, headers={}, body=body)


@pytest.mark.asyncio
async def test_cancel_returns_configured_value():
    adapter = MockProviderAdapter()
    assert await adapter.cancel_request(
        provider=_provider({"cancel_succeeds": True}),
        external_order_id="x",
        timeout_ms=500,
    )
    assert not await adapter.cancel_request(
        provider=_provider({"cancel_succeeds": False}),
        external_order_id="x",
        timeout_ms=500,
    )
