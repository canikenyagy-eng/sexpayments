"""Unit tests for the shared SSRF guard used by outbound callback delivery.

The merchant controls the webhook URL; the guard must reject non-http(s)
schemes and any host resolving to a private/loopback/link-local/metadata
address before the worker ever connects.
"""
import pytest

import app.core.ssrf as ssrf
from app.core.ssrf import SsrfError, assert_public_url, is_blocked_ip


@pytest.mark.parametrize(
    "ip",
    ["127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1",
     "169.254.169.254", "::1", "fc00::1", "0.0.0.0", "not-an-ip"],
)
def test_is_blocked_ip_blocks_non_public(ip):
    assert is_blocked_ip(ip) is True


@pytest.mark.parametrize("ip", ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"])
def test_is_blocked_ip_allows_public(ip):
    assert is_blocked_ip(ip) is False


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/1", "ftp://h/f", "", "notaurl"])
def test_assert_public_url_rejects_bad_scheme_or_host(url):
    with pytest.raises(SsrfError):
        assert_public_url(url)


def test_assert_public_url_blocks_metadata_host(monkeypatch):
    monkeypatch.setattr(ssrf, "resolve_ips", lambda host: ["169.254.169.254"])
    with pytest.raises(SsrfError):
        assert_public_url("http://metadata.example/latest/meta-data/")


def test_assert_public_url_blocks_when_any_ip_is_private(monkeypatch):
    monkeypatch.setattr(ssrf, "resolve_ips", lambda host: ["93.184.216.34", "10.0.0.5"])
    with pytest.raises(SsrfError):
        assert_public_url("https://rebind.example/webhook")


def test_assert_public_url_allows_public(monkeypatch):
    monkeypatch.setattr(ssrf, "resolve_ips", lambda host: ["93.184.216.34"])
    assert_public_url("https://api.merchant.example/webhook")  # must not raise


@pytest.mark.asyncio
async def test_transport_pins_validated_ip_and_keeps_host(monkeypatch):
    """PublicOnlyTransport (the connect-time anti-rebinding guard) rewrites the
    connect host to the validated public IP while preserving the original Host
    header + TLS SNI."""
    import httpx
    from app.core.ssrf import PublicOnlyTransport

    monkeypatch.setattr(ssrf, "resolve_ips", lambda host: ["93.184.216.34"])
    seen = {}

    async def fake_super(self, request):
        seen["host"] = request.url.host
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_super)
    req = httpx.Request("POST", "https://api.merchant.example/webhook")
    await PublicOnlyTransport().handle_async_request(req)
    assert seen["host"] == "93.184.216.34"          # connected to the validated IP
    assert seen["sni"] == "api.merchant.example"    # SNI/Host preserved


@pytest.mark.asyncio
async def test_transport_rejects_rebound_private_ip(monkeypatch):
    """A host that re-resolves to a private IP at connect time is rejected by the
    transport (DNS-rebinding defence), never reaching the network."""
    import httpx
    from app.core.ssrf import PublicOnlyTransport

    monkeypatch.setattr(ssrf, "resolve_ips", lambda host: ["10.0.0.5"])
    reached = {"net": False}

    async def fake_super(self, request):
        reached["net"] = True
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_super)
    req = httpx.Request("POST", "https://rebind.example/webhook")
    with pytest.raises(SsrfError):
        await PublicOnlyTransport().handle_async_request(req)
    assert reached["net"] is False
