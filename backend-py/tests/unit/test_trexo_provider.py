"""Unit tests for the TREXO receipt-check adapter.

Network is mocked via `httpx.AsyncClient.post` / `.get`. We exercise the
status-code → refundable mapping that the service relies on (e.g. 5xx is
refundable, a 200 with a dirty verdict is NOT).
"""
from __future__ import annotations

import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.modules.receipt_checks.models import ReceiptCheckProvider
from app.modules.receipt_checks.providers.trexo import TrexoClient


@pytest.fixture
def provider():
    p = MagicMock(spec=ReceiptCheckProvider)
    p.id = 1
    p.adapter_type = "trexo"
    p.base_url = "https://api.trexo.example"
    p.api_key_encrypted = "encrypted::token"
    p.price_usdt = Decimal("0.50")
    p.request_timeout_ms = 90000
    p.settings = {}
    return p


@pytest.fixture
def client(provider):
    return TrexoClient(provider)


@pytest.fixture
def pdf_path(tmp_path):
    p = tmp_path / "receipt.pdf"
    p.write_bytes(b"%PDF-1.4\n%dummy\n")
    return str(p)


# ── check_file — happy path ─────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.trexo.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_check_file_success_clean(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "sk_live_plain"
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "check_id": 8421,
        "transaction_id": "api:tx:1",
        "is_clean": True,
        "verdict": [],
        "data": {"sum": "1000", "currency": "RUB"},
        "remaining": 96,
    }
    mock_post.return_value = response

    result = await client.check_file(pdf_path)

    assert result.is_clean is True
    assert result.error_code is None
    assert result.refundable is False
    assert result.provider_check_id == "8421"
    assert result.provider_tx_id == "api:tx:1"
    assert result.parsed_data == {"sum": "1000", "currency": "RUB"}
    # Authorization header is set with the decrypted key
    headers = mock_post.call_args.kwargs.get("headers") or mock_post.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer sk_live_plain"


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.trexo.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_check_file_success_dirty_returns_verdict(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "sk_live_plain"
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "check_id": 9000,
        "transaction_id": "api:tx:2",
        "is_clean": False,
        "verdict": [{"type": "PROOF_EXISTS"}, {"type": "FAKE_PROOF"}],
        "data": {},
        "remaining": 1,
    }
    mock_post.return_value = response

    result = await client.check_file(pdf_path)

    assert result.is_clean is False
    # billed → not refundable
    assert result.refundable is False
    assert [v.type for v in result.verdict] == ["PROOF_EXISTS", "FAKE_PROOF"]


# ── check_file — refundable error paths ─────────────────────────────────


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.trexo.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_check_file_402_quota_exhausted_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "sk_live_plain"
    response = MagicMock()
    response.status_code = 402
    response.json.return_value = {"error": "quota_exhausted", "message": "out"}
    mock_post.return_value = response

    result = await client.check_file(pdf_path)

    assert result.error_code == "quota_exhausted"
    assert result.refundable is True
    assert result.is_clean is None


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.trexo.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_check_file_502_upstream_error_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "sk_live_plain"
    response = MagicMock()
    response.status_code = 502
    response.json.return_value = {"error": "upstream_error", "message": "verification offline"}
    mock_post.return_value = response

    result = await client.check_file(pdf_path)
    assert result.error_code == "upstream_error"
    assert result.refundable is True


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.trexo.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_check_file_504_timeout_via_status_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "sk_live_plain"
    response = MagicMock()
    response.status_code = 504
    response.json.return_value = {"error": "upstream_timeout", "message": "timeout"}
    mock_post.return_value = response

    result = await client.check_file(pdf_path)
    assert result.error_code == "upstream_timeout"
    assert result.refundable is True


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.trexo.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_check_file_400_invalid_file_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "sk_live_plain"
    response = MagicMock()
    response.status_code = 400
    response.json.return_value = {"error": "invalid_file", "message": "not PDF"}
    mock_post.return_value = response

    result = await client.check_file(pdf_path)
    assert result.error_code == "invalid_file"
    assert result.refundable is True


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.trexo.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_check_file_401_unauthorized_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "sk_live_plain"
    response = MagicMock()
    response.status_code = 401
    response.json.return_value = {"error": "unauthorized", "message": "bad key"}
    mock_post.return_value = response

    result = await client.check_file(pdf_path)
    assert result.error_code == "unauthorized"
    assert result.refundable is True


# ── check_file — non-status failures ────────────────────────────────────


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.trexo.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_check_file_timeout_exception_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "sk_live_plain"
    mock_post.side_effect = httpx.TimeoutException("boom")

    result = await client.check_file(pdf_path)
    assert result.error_code == "upstream_timeout"
    assert result.refundable is True


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.trexo.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_check_file_request_error_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "sk_live_plain"
    mock_post.side_effect = httpx.RequestError("dns failed")

    result = await client.check_file(pdf_path)
    assert result.error_code == "upstream_error"
    assert result.refundable is True


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.trexo.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_check_file_non_json_response_is_refundable(mock_post, mock_decrypt, client, pdf_path):
    mock_decrypt.return_value = "sk_live_plain"
    response = MagicMock()
    response.status_code = 200
    response.json.side_effect = ValueError("not json")
    response.text = "garbage"
    mock_post.return_value = response

    result = await client.check_file(pdf_path)
    assert result.error_code == "upstream_error"
    assert result.refundable is True


@pytest.mark.asyncio
async def test_check_file_missing_on_disk_is_refundable(client):
    result = await client.check_file("/tmp/does/not/exist/forever.pdf")
    assert result.error_code == "invalid_file"
    assert result.refundable is True


@pytest.mark.asyncio
async def test_check_file_without_api_key_raises(provider, pdf_path):
    provider.api_key_encrypted = None
    c = TrexoClient(provider)
    with pytest.raises(RuntimeError):
        await c.check_file(pdf_path)


# ── get_balance ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.trexo.decrypt_api_secret")
@patch("httpx.AsyncClient.get")
async def test_get_balance_success(mock_get, mock_decrypt, client):
    mock_decrypt.return_value = "sk_live_plain"
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"remaining": 96, "own": 90, "gifted": 6, "total_checks": 1284}
    mock_get.return_value = response

    result = await client.get_balance()
    assert result == {"remaining": 96, "own": 90, "gifted": 6, "total_checks": 1284}


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.trexo.decrypt_api_secret")
@patch("httpx.AsyncClient.get")
async def test_get_balance_http_error_returns_error_dict(mock_get, mock_decrypt, client):
    mock_decrypt.return_value = "sk_live_plain"
    response = MagicMock()
    response.status_code = 401
    response.json.return_value = {"error": "unauthorized", "message": "bad token"}
    mock_get.return_value = response

    result = await client.get_balance()
    assert "error" in result


@pytest.mark.asyncio
@patch("app.modules.receipt_checks.providers.trexo.decrypt_api_secret")
@patch("httpx.AsyncClient.get")
async def test_get_balance_network_error_returns_error_dict(mock_get, mock_decrypt, client):
    mock_decrypt.return_value = "sk_live_plain"
    mock_get.side_effect = httpx.RequestError("dns")

    result = await client.get_balance()
    assert "error" in result
