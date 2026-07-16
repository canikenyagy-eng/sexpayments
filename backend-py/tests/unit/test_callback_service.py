import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json
import hashlib
import hmac

from app.modules.callbacks.service import CallbackService
from app.modules.orders.models import Order
from app.modules.merchants.models import Merchant
from app.common.enums.orders import OrderStatus
from app.common.enums.finances import Currency
from app.core.exceptions import NotFoundException, CallbackRetryException

class AsyncContextManagerMock:
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        pass

@pytest.fixture
def mock_session():
    session = MagicMock()
    session.begin.return_value = AsyncContextManagerMock()
    session.execute = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session

@pytest.fixture
def service(mock_session):
    svc = CallbackService(mock_session)
    svc.attempt_repo = AsyncMock()
    # No dispute on the order by default; a test that needs one overrides this.
    svc._get_order_dispute = AsyncMock(return_value=None)
    return svc

@pytest.fixture
def mock_order():
    from datetime import datetime
    import uuid as uuid_mod
    order = MagicMock(spec=Order)
    order.id = 100
    order.uuid = uuid_mod.UUID("12345678-1234-5678-1234-567812345678")
    order.merchant_id = 10
    order.external_id = "ext-123"
    order.client_user_id = None
    order.amount = 1000.0
    order.amount_usdt = None
    order.fee_usdt = None
    order.exchange_rate = None
    order.currency = Currency.RUB
    order.status = OrderStatus.SUCCESS
    order.payment_url = "https://checkout.example.com/pay/123"
    order.webhook_url = "https://example.com/webhook"
    order.created_at = datetime(2026, 1, 1, 10, 0, 0)
    order.date_end = None
    order.requisite = None
    order.payment_option = None
    return order

@pytest.fixture
def mock_merchant():
    merchant = MagicMock(spec=Merchant)
    merchant.id = 10
    merchant.name = "Test Merchant"
    merchant.webhook_url = "https://merchant.com/webhook"
    merchant.api_secret = "encrypted_secret"
    return merchant


@pytest.fixture(autouse=True)
def _stub_dns(monkeypatch):
    """send_callback now runs the SSRF guard (assert_public_url), which resolves
    the webhook host via DNS. Pin it to a public IP so these unit tests stay
    offline (no live getaddrinfo); the SSRF-block path overrides this per-test.
    """
    monkeypatch.setattr("app.core.ssrf.resolve_ips", lambda host: ["93.184.216.34"])

@pytest.mark.asyncio
async def test_get_order_with_merchant_success(service, mock_session, mock_order, mock_merchant):
    # Setup mock execute results
    mock_order_result = MagicMock()
    mock_order_scalars = MagicMock()
    mock_order_scalars.first.return_value = mock_order
    mock_order_result.scalars.return_value = mock_order_scalars
    
    mock_merchant_result = MagicMock()
    mock_merchant_scalars = MagicMock()
    mock_merchant_scalars.first.return_value = mock_merchant
    mock_merchant_result.scalars.return_value = mock_merchant_scalars
    
    mock_session.execute.side_effect = [mock_order_result, mock_merchant_result]
    
    order, merchant = await service._get_order_with_merchant(100)
    
    assert order == mock_order
    assert merchant == mock_merchant
    assert mock_session.execute.call_count == 2

@pytest.mark.asyncio
async def test_get_order_with_merchant_order_not_found(service, mock_session):
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    mock_scalars.first.return_value = None
    mock_result.scalars.return_value = mock_scalars
    
    mock_session.execute.return_value = mock_result
    
    with pytest.raises(NotFoundException, match="Order 100 not found"):
        await service._get_order_with_merchant(100)

@pytest.mark.asyncio
async def test_get_order_with_merchant_merchant_not_found(service, mock_session, mock_order):
    mock_order_result = MagicMock()
    mock_order_scalars = MagicMock()
    mock_order_scalars.first.return_value = mock_order
    mock_order_result.scalars.return_value = mock_order_scalars
    
    mock_merchant_result = MagicMock()
    mock_merchant_scalars = MagicMock()
    mock_merchant_scalars.first.return_value = None
    mock_merchant_result.scalars.return_value = mock_merchant_scalars
    
    mock_session.execute.side_effect = [mock_order_result, mock_merchant_result]
    
    with pytest.raises(NotFoundException, match="Merchant 10 not found"):
        await service._get_order_with_merchant(100)

def test_generate_signature(service):
    payload = {"amount": 100.0, "currency": "RUB", "order_id": 1}
    secret = "my_secret_key"
    
    signature = service._generate_signature(payload, secret)
    
    # Manually calculate to verify
    payload_str = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    expected_sig = hmac.new(
        secret.encode("utf-8"),
        payload_str.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    
    assert signature == expected_sig

def test_build_payload(service, mock_order, mock_merchant):
    payload = service._build_payload(mock_order, mock_merchant)

    assert payload["id"] == "12345678-1234-5678-1234-567812345678"
    assert payload["internalId"] == "ext-123"
    assert payload["merchant_name"] == "Test Merchant"
    assert payload["amount"] == 1000.0
    assert payload["amount_usdt"] is None
    assert payload["fee_usdt"] is None
    assert payload["exchange_rate"] is None
    assert payload["currency"] == "RUB"
    assert payload["status"] == "success"
    assert payload["payment_url"] == "https://checkout.example.com/pay/123"
    assert payload["created_at"] == "2026-01-01T10:00:00"
    assert payload["expires_at"] is None
    assert payload["requisite"] is None
    # No dispute on this order → no dispute block (backward-compatible).
    assert "dispute" not in payload


def test_build_payload_includes_dispute_when_present(service, mock_order, mock_merchant):
    """When the order has a dispute, the merchant callback carries its reason /
    status / substatus so the merchant can react without polling the API."""
    import uuid as uuid_mod
    from app.common.enums.disputes import DisputeReason, DisputeStatus
    dispute = MagicMock()
    dispute.uuid = uuid_mod.UUID("aaaaaaaa-1111-2222-3333-444444444444")
    dispute.status = DisputeStatus.RESOLVED
    dispute.reason = DisputeReason.INVALID_SUM
    dispute.substatus = None  # no pending proof request

    payload = service._build_payload(mock_order, mock_merchant, dispute=dispute)

    # Machine fields only — id / status / reason / substatus. The free-text
    # resolution stays human-facing (merchant API) — it must NOT leak here.
    assert payload["dispute"] == {
        "id": "aaaaaaaa-1111-2222-3333-444444444444",
        "status": "resolved",
        "reason": "invalid_sum",
        "substatus": None,
    }
    assert "resolution_text" not in payload["dispute"]


def test_build_payload_dispute_substatus_signals_proof_request(service, mock_order, mock_merchant):
    """A pending proof request surfaces in the callback as the dispute substatus
    (``pdf_requested`` / ``video_requested``) so the merchant knows to attach a
    PDF check or a video — no polling needed."""
    import uuid as uuid_mod
    from app.common.enums.disputes import DisputeReason, DisputeStatus, DisputeSubstatus
    dispute = MagicMock()
    dispute.uuid = uuid_mod.UUID("bbbbbbbb-1111-2222-3333-444444444444")
    dispute.status = DisputeStatus.OPEN
    dispute.reason = DisputeReason.INVALID_SUM
    dispute.substatus = DisputeSubstatus.VIDEO_REQUESTED

    payload = service._build_payload(mock_order, mock_merchant, dispute=dispute)

    assert payload["dispute"]["status"] == "open"
    assert payload["dispute"]["substatus"] == "video_requested"


@pytest.mark.asyncio
@patch("app.core.security.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_send_callback_success(mock_post, mock_decrypt, service, mock_order, mock_merchant):
    service._get_order_with_merchant = AsyncMock(return_value=(mock_order, mock_merchant))
    mock_decrypt.return_value = "plain_secret"
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.text = "OK"
    mock_post.return_value = mock_response
    
    result = await service.send_callback(100, attempt_number=1)
    
    assert result is True
    service._get_order_with_merchant.assert_called_once_with(100)
    mock_decrypt.assert_called_once_with("encrypted_secret")
    mock_post.assert_called_once()
    
    # Verify post args: we now send the canonical body via ``content=`` (raw
    # bytes) so the HMAC payload equals the on-wire bytes. The id lives inside
    # those bytes — decode and parse to assert.
    call_args = mock_post.call_args
    assert call_args[0][0] == "https://example.com/webhook"  # Order webhook takes precedence
    assert "X-Signature" in call_args[1]["headers"]
    sent_body = call_args[1]["content"]
    assert isinstance(sent_body, bytes)
    assert json.loads(sent_body)["id"] == "12345678-1234-5678-1234-567812345678"
    # And the X-Signature must verify against those exact bytes.
    expected_sig = hmac.new(b"plain_secret", sent_body, hashlib.sha256).hexdigest()
    assert call_args[1]["headers"]["X-Signature"] == expected_sig
    
    # Verify attempt repo
    service.attempt_repo.create.assert_called_once()
    create_args = service.attempt_repo.create.call_args[0][0]
    assert create_args["order_id"] == 100
    assert create_args["url"] == "https://example.com/webhook"
    assert create_args["response_status"] == 200
    assert create_args["is_successful"] is True
    assert create_args["attempt_number"] == 1

    # The attempt row must be committed; otherwise the worker session closes
    # without persisting the log and the admin Callbacks page stays empty.
    service.session.commit.assert_awaited_once()

@pytest.mark.asyncio
async def test_send_callback_no_url(service, mock_order, mock_merchant):
    mock_order.webhook_url = None
    mock_merchant.webhook_url = None
    service._get_order_with_merchant = AsyncMock(return_value=(mock_order, mock_merchant))
    
    result = await service.send_callback(100)
    
    assert result is True
    service.attempt_repo.create.assert_not_called()

@pytest.mark.asyncio
@patch("app.core.security.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_send_callback_http_error(mock_post, mock_decrypt, service, mock_order, mock_merchant):
    service._get_order_with_merchant = AsyncMock(return_value=(mock_order, mock_merchant))
    mock_decrypt.return_value = "plain_secret"
    
    import httpx
    mock_post.side_effect = httpx.RequestError("Connection failed")
    
    with pytest.raises(CallbackRetryException, match="Callback failed with status None"):
        await service.send_callback(100)

    service.attempt_repo.create.assert_called_once()
    create_args = service.attempt_repo.create.call_args[0][0]
    assert create_args["is_successful"] is False
    assert create_args["response_status"] is None
    assert "Connection failed" in create_args["response_body"]

    # Even when the callback fails we must commit the attempt log before
    # re-raising, so the failure is visible in the admin Callbacks page.
    service.session.commit.assert_awaited_once()

@pytest.mark.asyncio
@patch("app.core.security.decrypt_api_secret")
@patch("httpx.AsyncClient.post")
async def test_send_callback_non_200_status(mock_post, mock_decrypt, service, mock_order, mock_merchant):
    service._get_order_with_merchant = AsyncMock(return_value=(mock_order, mock_merchant))
    mock_decrypt.return_value = "plain_secret"
    
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal Server Error"
    mock_post.return_value = mock_response
    
    with pytest.raises(CallbackRetryException, match="Callback failed with status 500"):
        await service.send_callback(100)

    service.attempt_repo.create.assert_called_once()
    create_args = service.attempt_repo.create.call_args[0][0]
    assert create_args["is_successful"] is False
    assert create_args["response_status"] == 500
    service.session.commit.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.core.security.decrypt_api_secret")
async def test_send_callback_blocks_ssrf_url(mock_decrypt, service, mock_order, mock_merchant, monkeypatch):
    """A merchant webhook_url resolving to a private/metadata address is blocked
    by the SSRF guard BEFORE any outbound request: no POST, a recorded failed
    attempt ('blocked by SSRF guard'), and a False return (permanent — no retry)."""
    service._get_order_with_merchant = AsyncMock(return_value=(mock_order, mock_merchant))
    mock_decrypt.return_value = "plain_secret"
    monkeypatch.setattr("app.core.ssrf.resolve_ips", lambda host: ["169.254.169.254"])

    with patch("httpx.AsyncClient.post") as mock_post:
        result = await service.send_callback(100, attempt_number=1)
        mock_post.assert_not_called()

    assert result is False
    create_args = service.attempt_repo.create.call_args[0][0]
    assert create_args["is_successful"] is False
    assert "blocked by SSRF guard" in create_args["response_body"]
    service.session.commit.assert_awaited()
