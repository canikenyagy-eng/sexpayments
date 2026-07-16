"""Unit tests for ProviderAdapter's base signing/verification helpers.

These cover the contract every new adapter inherits for free:
  * canonical_json — sort keys, no whitespace, "{}" for empty/None
  * build_signature_payload — "{ts}.{METHOD}.{path}{body}"
  * compute_signature — HMAC, hex/base64, alg switch
  * sign_request — full headers dict with Authorization / X-Timestamp /
                   X-Signature / X-Idempotency-Key, extras merged
  * verify_callback_signature — accepts matching hex(HMAC(body)), raises on
                                mismatch, raises when header is missing
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from typing import Optional

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://unused:unused@localhost:5432/unused")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-tests-must-be-long!")
os.environ.setdefault("ADMIN_USERNAME", "admin")
os.environ.setdefault("ADMIN_PASSWORD", "admin123")

import pytest

from app.common.enums.payments import PaymentMethod
from app.modules.cascading.integrations.base import (
    CallbackVerificationError,
    IssueResult,
    ParsedCallback,
    ProviderAdapter,
)
from app.modules.cascading.models import CascadeProvider


class _MinimalAdapter(ProviderAdapter):
    """Bare-bones adapter — uses only base-class signing defaults."""

    code = "minimal"

    def supports(self, *, provider, method, payment_option_code):
        return True

    async def issue_requisite(self, *, provider, order_data, idempotency_key, timeout_ms):
        raise NotImplementedError

    async def cancel_request(self, *, provider, external_order_id, timeout_ms):
        raise NotImplementedError

    async def notify_receipt(self, *, provider, external_order_id, receipt_path, comment):
        raise NotImplementedError

    async def raise_dispute(self, *, provider, external_order_id, reason, evidence_paths):
        raise NotImplementedError

    def parse_callback(self, *, provider, headers, body):
        raise NotImplementedError

    def parse_payin_response(self, *, provider, resp, order_data, fallback_method):
        raise NotImplementedError


class _Base64Adapter(_MinimalAdapter):
    code = "b64"
    SIGNATURE_ENCODING = "base64"


class _SHA512Adapter(_MinimalAdapter):
    code = "sha512"
    SIGNATURE_ALGORITHM = "sha512"


class _CustomLayoutAdapter(_MinimalAdapter):
    code = "custom"

    def build_signature_payload(self, *, timestamp, method, path, body_json):
        # Newline-separated layout, no method, no body.
        return f"{timestamp}\n{path}"


class _CustomHeaderAdapter(_MinimalAdapter):
    code = "custom_headers"
    AUTH_HEADER = "X-Api-Token"
    AUTH_SCHEME = ""  # bare token, no "Bearer "
    TIMESTAMP_HEADER = "X-Time"
    SIGNATURE_HEADER = "X-Sign"
    IDEMPOTENCY_HEADER = "X-Idem"


# ─── canonical_json ─────────────────────────────────────────


def test_canonical_json_empty():
    a = _MinimalAdapter()
    assert a.canonical_json(None) == "{}"
    assert a.canonical_json({}) == "{}"


def test_canonical_json_sorts_keys():
    a = _MinimalAdapter()
    assert a.canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'


def test_canonical_json_no_whitespace():
    a = _MinimalAdapter()
    out = a.canonical_json({"a": [1, 2, 3], "nested": {"x": 1}})
    assert " " not in out
    assert "\n" not in out


def test_canonical_json_unicode_preserved():
    a = _MinimalAdapter()
    # ensure_ascii=False keeps Cyrillic/emoji literal, matching most provider signing rules.
    assert a.canonical_json({"name": "Иван"}) == '{"name":"Иван"}'


# ─── build_signature_payload ────────────────────────────────


def test_build_payload_default_layout():
    a = _MinimalAdapter()
    payload = a.build_signature_payload(
        timestamp="1700000000",
        method="post",
        path="/x",
        body_json='{"a":1}',
    )
    assert payload == '1700000000.POST./x{"a":1}'


def test_build_payload_method_upcased():
    a = _MinimalAdapter()
    p1 = a.build_signature_payload(timestamp="1", method="get", path="/x", body_json="{}")
    p2 = a.build_signature_payload(timestamp="1", method="GET", path="/x", body_json="{}")
    assert p1 == p2


def test_build_payload_overrideable():
    a = _CustomLayoutAdapter()
    payload = a.build_signature_payload(
        timestamp="42", method="POST", path="/x", body_json='{"y":1}'
    )
    assert payload == "42\n/x"


# ─── compute_signature ──────────────────────────────────────


def test_compute_signature_hex_default():
    a = _MinimalAdapter()
    expected = hmac.new(b"s", b"data", hashlib.sha256).hexdigest()
    assert a.compute_signature(secret="s", payload="data") == expected


def test_compute_signature_base64():
    a = _Base64Adapter()
    raw = hmac.new(b"s", b"data", hashlib.sha256).digest()
    assert a.compute_signature(secret="s", payload="data") == base64.b64encode(raw).decode()


def test_compute_signature_sha512():
    a = _SHA512Adapter()
    expected = hmac.new(b"s", b"data", hashlib.sha512).hexdigest()
    assert a.compute_signature(secret="s", payload="data") == expected


def test_compute_signature_accepts_bytes_payload():
    a = _MinimalAdapter()
    expected = hmac.new(b"s", b"\x00\x01\x02", hashlib.sha256).hexdigest()
    assert a.compute_signature(secret="s", payload=b"\x00\x01\x02") == expected


def test_compute_signature_bad_algorithm_raises():
    class _Bad(_MinimalAdapter):
        SIGNATURE_ALGORITHM = "shaXYZ"

    with pytest.raises(ValueError):
        _Bad().compute_signature(secret="s", payload="x")


def test_compute_signature_bad_encoding_raises():
    class _Bad(_MinimalAdapter):
        SIGNATURE_ENCODING = "rot13"

    with pytest.raises(ValueError):
        _Bad().compute_signature(secret="s", payload="x")


# ─── sign_request ───────────────────────────────────────────


def test_sign_request_headers_default():
    a = _MinimalAdapter()
    headers = a.sign_request(token="tok", method="POST", path="/x", body={"a": 1})
    assert headers["Authorization"] == "Bearer tok"
    assert "X-Timestamp" in headers
    assert "X-Signature" in headers
    # Signature must verify with the same primitives.
    expected = a.compute_signature(
        secret="tok",
        payload=a.build_signature_payload(
            timestamp=headers["X-Timestamp"],
            method="POST",
            path="/x",
            body_json=a.canonical_json({"a": 1}),
        ),
    )
    assert headers["X-Signature"] == expected
    # No idempotency unless explicitly requested.
    assert "X-Idempotency-Key" not in headers


def test_sign_request_with_idempotency_key():
    a = _MinimalAdapter()
    headers = a.sign_request(
        token="tok", method="POST", path="/x", body=None, idempotency_key="abc"
    )
    assert headers["X-Idempotency-Key"] == "abc"


def test_sign_request_extra_headers_merged():
    a = _MinimalAdapter()
    headers = a.sign_request(
        token="tok",
        method="POST",
        path="/x",
        body=None,
        extra_headers={"X-Client": "primepay", "User-Agent": "cascade/1"},
    )
    assert headers["X-Client"] == "primepay"
    assert headers["User-Agent"] == "cascade/1"


def test_sign_request_custom_headers_class_attrs():
    a = _CustomHeaderAdapter()
    headers = a.sign_request(
        token="tok", method="POST", path="/x", body={"a": 1}, idempotency_key="abc"
    )
    # Custom header names from class attrs.
    assert headers["X-Api-Token"] == "tok"  # AUTH_SCHEME is empty → bare token
    assert "X-Time" in headers
    assert "X-Sign" in headers
    assert headers["X-Idem"] == "abc"
    # Default header names absent.
    assert "Authorization" not in headers
    assert "X-Signature" not in headers


# ─── verify_callback_signature ──────────────────────────────


def _make_provider() -> CascadeProvider:
    """Build a CascadeProvider stub directly — model column dicts aren't touched."""
    return CascadeProvider()  # all column defaults, none of which we read here


def test_verify_callback_accepts_correct_signature():
    a = _MinimalAdapter()
    body = b'{"event":"x"}'
    sig = a.compute_signature(secret="wh-secret", payload=body)
    # Should not raise.
    a.verify_callback_signature(
        secret="wh-secret",
        headers={"X-Signature": sig},
        body=body,
    )


def test_verify_callback_rejects_bad_signature():
    a = _MinimalAdapter()
    with pytest.raises(CallbackVerificationError):
        a.verify_callback_signature(
            secret="wh-secret",
            headers={"X-Signature": "deadbeef"},
            body=b'{"event":"x"}',
        )


def test_verify_callback_rejects_missing_header():
    a = _MinimalAdapter()
    with pytest.raises(CallbackVerificationError):
        a.verify_callback_signature(
            secret="wh-secret",
            headers={},
            body=b'{"event":"x"}',
        )


def test_verify_callback_case_insensitive_header_lookup():
    a = _MinimalAdapter()
    body = b"{}"
    sig = a.compute_signature(secret="s", payload=body)
    # Real HTTP clients normalize header casing differently — must work regardless.
    a.verify_callback_signature(
        secret="s",
        headers={"x-signature": sig},
        body=body,
    )


def test_verify_callback_with_custom_header():
    """When SIGNATURE_HEADER is overridden, base verify_callback reads from it."""

    class _A(_MinimalAdapter):
        SIGNATURE_HEADER = "X-My-Sig"

    a = _A()
    body = b"{}"
    sig = a.compute_signature(secret="s", payload=body)
    a.verify_callback_signature(
        secret="s",
        headers={"X-My-Sig": sig},
        body=body,
    )
    with pytest.raises(CallbackVerificationError):
        # The default X-Signature is NOT honored when the adapter chose another header.
        a.verify_callback_signature(
            secret="s",
            headers={"X-Signature": sig},
            body=body,
        )


def test_legacy_crypto_signing_matches_base_defaults():
    """LegacyCrypto's documented scheme is the base default — sanity check."""
    from app.modules.cascading.integrations.legacy_crypto import LegacyCryptoAdapter

    a = LegacyCryptoAdapter()
    headers = a.sign_request(
        token="tok", method="POST", path="/x", body={"a": 1}, idempotency_key="k"
    )
    assert headers["Authorization"].startswith("Bearer ")
    assert "X-Timestamp" in headers
    assert "X-Signature" in headers
    assert headers["X-Idempotency-Key"] == "k"
