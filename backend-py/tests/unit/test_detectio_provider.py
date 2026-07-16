"""Unit tests for the detect.io receipt-check adapter.

Network is mocked via `httpx.AsyncClient.post` / `.get`. The focus is the
verdict/status → (is_clean, error_code, refundable) mapping the service relies
on:
  * `original` / `fake` are billed on detect.io's side → refundable=False;
  * `unknown_bank` / `cannot_process` and every HTTP error are free → the
    adapter sets an error_code + refundable=True so the service reverses the
    trader's USDT charge.
"""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import MagicMock, patch

import httpx
import pytest

from app.modules.receipt_checks.models import ReceiptCheckProvider
from app.modules.receipt_checks.providers.detectio import DetectioClient


@pytest.fixture
def provider():
    p = MagicMock(spec=ReceiptCheckProvider)
    p.id = 1
    p.adapter_type = "detectio"
    p.base_url = "https://cheque.wales/api/v1"
    p.api_key_encrypted = "encrypted::token"
    p.price_usdt = Decimal("0.10")
    p.request_timeout_ms = 90000
    p.settings = {}
    return p


@pytest.fixture
def client(provider):
    return DetectioClient(provider)


@pytest.fixture
def pdf_path(tmp_path):
    p = tmp_path / "receipt.pdf"
    p.write_bytes(b"%PDF-1.4\n%dummy\n")
    return str(p)


def _resp(status: int, body):
    r = MagicMock()
    r.status_code = status
    if isinstance(body, Exception):
        r.json.side_effect = body
        r.text = "garbage"
    else:
        r.json.return_value = body
    return r


# ── check_file — billable verdicts (SUCCESS) ────────────────────────────


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_verify_original_is_clean_and_billed(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "tok_plain"
    mock_post.return_value = _resp(200, {
        "request_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
        "verdict": "original",
        "bank": "tbank_new",
        "checked_at": "2026-06-17T09:30:00Z",
    })

    result = await client.check_file(pdf_path)

    assert result.is_clean is True
    assert result.error_code is None
    assert result.refundable is False
    assert result.provider_check_id == "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
    assert result.verdict == []  # clean → no red flags
    assert result.parsed_data["bank"] == "tbank_new"
    assert result.parsed_data["verdict"] == "original"
    # POST hits /verify with a Bearer token + multipart file
    args, kwargs = mock_post.call_args
    assert (args[0] if args else kwargs.get("url")).endswith("/verify")
    assert kwargs["headers"]["Authorization"] == "Bearer tok_plain"
    assert "file" in kwargs["files"]


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_verify_fake_is_dirty_and_billed(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "tok_plain"
    mock_post.return_value = _resp(200, {
        "request_id": "req-2", "verdict": "fake", "bank": "sber", "checked_at": "x",
    })

    result = await client.check_file(pdf_path)

    assert result.is_clean is False
    assert result.error_code is None
    assert result.refundable is False
    assert [v.type for v in result.verdict] == ["fake"]
    assert "sber" in (result.verdict[0].message or "")


# ── check_file — free, non-decisive verdicts (FAILED + refund) ──────────


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_verify_unknown_bank_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "tok_plain"
    mock_post.return_value = _resp(200, {"request_id": "req-3", "verdict": "unknown_bank"})

    result = await client.check_file(pdf_path)

    assert result.is_clean is None
    assert result.error_code == "unknown_bank"
    assert result.refundable is True
    assert result.provider_check_id == "req-3"


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_verify_cannot_process_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "tok_plain"
    mock_post.return_value = _resp(200, {"request_id": "req-4", "verdict": "cannot_process"})

    result = await client.check_file(pdf_path)

    assert result.error_code == "cannot_process"
    assert result.refundable is True
    assert result.is_clean is None


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_verify_unexpected_verdict_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "tok_plain"
    mock_post.return_value = _resp(200, {"request_id": "req-5", "verdict": "wat"})

    result = await client.check_file(pdf_path)
    assert result.error_code == "unexpected_verdict"
    assert result.refundable is True


# ── check_file — HTTP errors (all refundable) ───────────────────────────


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_verify_402_insufficient_balance_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "tok_plain"
    mock_post.return_value = _resp(402, {
        "error": "insufficient_balance", "balance": "0.05", "price": "0.10", "currency": "USDT",
    })

    result = await client.check_file(pdf_path)
    assert result.error_code == "insufficient_balance"
    assert result.refundable is True
    assert result.is_clean is None
    assert "0.05" in result.error_message and "0.10" in result.error_message


@pytest.mark.parametrize(
    "status,expected_code",
    [
        (400, "invalid_request"),
        (401, "unauthorized"),
        (404, "not_found"),
        (500, "internal_error"),
    ],
)
@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_verify_http_error_codes_are_refundable(mock_post, mock_decrypt, client, pdf_path, status, expected_code):
    mock_decrypt.return_value = "tok_plain"
    # No "error" field in body → adapter derives the code from the status.
    mock_post.return_value = _resp(status, {"message": "nope"})

    result = await client.check_file(pdf_path)
    assert result.error_code == expected_code
    assert result.refundable is True


@pytest.mark.parametrize("status", [403, 429])
@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_verify_undocumented_error_status_still_refundable(mock_post, mock_decrypt, client, pdf_path, status):
    """Any non-200 means no verdict → not billed → must refund, even for
    statuses the docs don't enumerate (403/429)."""
    mock_decrypt.return_value = "tok_plain"
    mock_post.return_value = _resp(status, {"message": "nope"})
    result = await client.check_file(pdf_path)
    assert result.refundable is True
    assert result.is_clean is None


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_verify_error_field_overrides_status(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "tok_plain"
    mock_post.return_value = _resp(401, {"error": "unauthorized", "message": "bad token"})
    result = await client.check_file(pdf_path)
    assert result.error_code == "unauthorized"
    assert result.refundable is True


# ── check_file — transport / disk failures ──────────────────────────────


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_verify_timeout_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "tok_plain"
    mock_post.side_effect = httpx.TimeoutException("boom")
    result = await client.check_file(pdf_path)
    assert result.error_code == "upstream_timeout"
    assert result.refundable is True


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_verify_request_error_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "tok_plain"
    mock_post.side_effect = httpx.RequestError("dns failed")
    result = await client.check_file(pdf_path)
    assert result.error_code == "upstream_error"
    assert result.refundable is True


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_verify_non_json_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "tok_plain"
    mock_post.return_value = _resp(200, ValueError("not json"))
    result = await client.check_file(pdf_path)
    assert result.error_code == "upstream_error"
    assert result.refundable is True


@pytest.mark.asyncio
async def test_verify_missing_file_is_refundable(client):
    result = await client.check_file("/tmp/does/not/exist/forever.pdf")
    assert result.error_code == "invalid_file"
    assert result.refundable is True


@pytest.mark.asyncio
async def test_verify_without_api_key_raises(provider, pdf_path):
    provider.api_key_encrypted = None
    c = DetectioClient(provider)
    with pytest.raises(RuntimeError):
        await c.check_file(pdf_path)


# ── get_balance — USDT money balance → affordable checks ────────────────


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.get")
async def test_get_balance_maps_to_affordable_checks(mock_get, mock_decrypt, client):
    mock_decrypt.return_value = "tok_plain"
    mock_get.return_value = _resp(200, {
        "balance": "12.50", "price": "0.10", "currency": "USDT", "enabled": True,
    })

    result = await client.get_balance()
    assert result == {"remaining": 125}  # floor(12.50 / 0.10)
    args, kwargs = mock_get.call_args
    assert (args[0] if args else kwargs.get("url")).endswith("/billing/balance")


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.get")
async def test_get_balance_zero_price_returns_none_remaining(mock_get, mock_decrypt, client):
    mock_decrypt.return_value = "tok_plain"
    mock_get.return_value = _resp(200, {"balance": "12.50", "price": "0", "enabled": True})
    result = await client.get_balance()
    assert result == {"remaining": None}


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.get")
async def test_get_balance_disabled_returns_error(mock_get, mock_decrypt, client):
    mock_decrypt.return_value = "tok_plain"
    mock_get.return_value = _resp(200, {"balance": "12.50", "price": "0.10", "enabled": False})
    result = await client.get_balance()
    assert "error" in result


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.get")
async def test_get_balance_http_error_returns_error_dict(mock_get, mock_decrypt, client):
    mock_decrypt.return_value = "tok_plain"
    mock_get.return_value = _resp(401, {"message": "bad token"})
    result = await client.get_balance()
    assert "error" in result


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.get")
async def test_get_balance_network_error_returns_error_dict(mock_get, mock_decrypt, client):
    mock_decrypt.return_value = "tok_plain"
    mock_get.side_effect = httpx.RequestError("dns")
    result = await client.get_balance()
    assert "error" in result


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.get")
async def test_get_balance_non_json_returns_clear_error(mock_get, mock_decrypt, client):
    """A wrong Base URL → HTML 404 → non-JSON body must yield a clear, actionable
    error (not a raw JSONDecodeError)."""
    mock_decrypt.return_value = "tok_plain"
    mock_get.return_value = _resp(404, ValueError("not json"))
    result = await client.get_balance()
    assert "error" in result
    assert "Base URL" in result["error"]


@pytest.mark.parametrize(
    "base_url",
    ["https://cheque.wales", "https://cheque.wales/", "https://cheque.wales/api/v1", "https://cheque.wales/api/v1/"],
)
@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.get")
async def test_base_url_normalizes_to_api_v1(mock_get, mock_decrypt, provider, base_url):
    """Whether the admin enters the host or the full /api/v1 root, requests hit
    exactly one /api/v1 — the fix for the balance 404."""
    provider.base_url = base_url
    mock_decrypt.return_value = "tok_plain"
    mock_get.return_value = _resp(200, {"balance": "10", "price": "0.1", "enabled": True})
    await DetectioClient(provider).get_balance()
    url = mock_get.call_args[0][0] if mock_get.call_args[0] else mock_get.call_args.kwargs["url"]
    assert url == "https://cheque.wales/api/v1/billing/balance"


# ── get_check — free re-fetch of a past result ──────────────────────────


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.get")
async def test_get_check_returns_payload(mock_get, mock_decrypt, client):
    mock_decrypt.return_value = "tok_plain"
    payload = {"request_id": "req-9", "verdict": "original", "bank": "sber"}
    mock_get.return_value = _resp(200, payload)

    result = await client.get_check("req-9")
    assert result == payload
    args, kwargs = mock_get.call_args
    assert (args[0] if args else kwargs.get("url")).endswith("/verify/req-9")


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.detectio.decrypt_api_secret")
@patch("httpx.AsyncClient.get")
async def test_get_check_not_found_returns_none(mock_get, mock_decrypt, client):
    mock_decrypt.return_value = "tok_plain"
    mock_get.return_value = _resp(404, {"error": "not_found"})
    result = await client.get_check("missing")
    assert result is None
