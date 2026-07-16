"""Unit tests for the SSRF-safe dispute-evidence downloader.

A merchant-supplied URL is fetched server-side, so the guard must block any URL
that resolves to a non-public address (loopback / private / link-local incl. the
cloud-metadata IP / reserved), reject non-http(s) schemes, cap the body size, and
validate the downloaded bytes against the allowed evidence formats.
"""
import httpx
import pytest

from app.core.exceptions import ValidationException
from app.modules.receipts import download as dl

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


# ── SSRF guard: IP classification ───────────────────────────────────────────

@pytest.mark.parametrize("ip", [
    "127.0.0.1", "::1",               # loopback
    "10.0.0.5", "192.168.1.10", "172.16.0.1",   # private
    "169.254.169.254",               # link-local / cloud metadata
    "0.0.0.0",                       # unspecified
    "240.0.0.1",                     # reserved
    "fc00::1",                       # IPv6 ULA (private)
])
def test_is_blocked_ip_blocks_non_public(ip):
    assert dl._is_blocked_ip(ip) is True


@pytest.mark.parametrize("ip", ["1.1.1.1", "8.8.8.8", "93.184.216.34"])
def test_is_blocked_ip_allows_public(ip):
    assert dl._is_blocked_ip(ip) is False


# ── SSRF guard: URL ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("url", [
    "ftp://example.com/x.png",
    "file:///etc/passwd",
    "gopher://example.com/",
    "//noscheme/x.png",
])
def test_assert_public_url_rejects_bad_scheme(url):
    with pytest.raises(ValidationException):
        dl._assert_public_url(url)


def test_assert_public_url_rejects_host_resolving_to_private(monkeypatch):
    monkeypatch.setattr(dl, "_resolve_ips", lambda host: ["127.0.0.1"])
    with pytest.raises(ValidationException):
        dl._assert_public_url("http://internal.local/secret.png")


def test_assert_public_url_allows_public(monkeypatch):
    monkeypatch.setattr(dl, "_resolve_ips", lambda host: ["93.184.216.34"])
    dl._assert_public_url("https://cdn.example.com/proof.png")  # no raise


# ── fetch_evidence ──────────────────────────────────────────────────────────

def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.mark.asyncio
async def test_fetch_evidence_returns_validated_file(monkeypatch):
    monkeypatch.setattr(dl, "_resolve_ips", lambda host: ["93.184.216.34"])

    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=_PNG)

    out = await dl.fetch_evidence("https://cdn.example.com/proof.png", client=_client(handler))
    assert out.content == _PNG
    assert out.filename.endswith(".png")
    assert out.mime == "image/png"


@pytest.mark.asyncio
async def test_fetch_evidence_rejects_oversize(monkeypatch):
    monkeypatch.setattr(dl, "_resolve_ips", lambda host: ["93.184.216.34"])
    monkeypatch.setattr("app.modules.receipts.download.settings.MAX_EVIDENCE_SIZE_MB", 0, raising=False)

    def handler(request):
        return httpx.Response(200, headers={"content-type": "image/png"}, content=_PNG + b"x" * 4096)

    with pytest.raises(ValidationException):
        await dl.fetch_evidence("https://cdn.example.com/big.png", client=_client(handler))


@pytest.mark.asyncio
async def test_fetch_evidence_rejects_bad_format(monkeypatch):
    monkeypatch.setattr(dl, "_resolve_ips", lambda host: ["93.184.216.34"])

    def handler(request):
        # claims png but bytes are not an image → validate_format rejects
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"<html>nope</html>")

    with pytest.raises(ValidationException):
        await dl.fetch_evidence("https://cdn.example.com/x.png", client=_client(handler))


@pytest.mark.asyncio
async def test_fetch_evidence_rejects_non_200(monkeypatch):
    monkeypatch.setattr(dl, "_resolve_ips", lambda host: ["93.184.216.34"])

    def handler(request):
        return httpx.Response(404, content=b"missing")

    with pytest.raises(ValidationException):
        await dl.fetch_evidence("https://cdn.example.com/missing.png", client=_client(handler))


@pytest.mark.asyncio
async def test_fetch_evidence_blocks_private_target(monkeypatch):
    monkeypatch.setattr(dl, "_resolve_ips", lambda host: ["169.254.169.254"])
    # Guard fires before any network call — no client needed.
    with pytest.raises(ValidationException):
        await dl.fetch_evidence("http://metadata/latest/meta-data/")


# ── DNS-pinning transport (anti TOCTOU / rebinding) ─────────────────────────

def test_pick_safe_ip_rejects_any_non_public(monkeypatch):
    monkeypatch.setattr(dl, "_resolve_ips", lambda h: ["93.184.216.34", "10.0.0.5"])
    with pytest.raises(ValidationException):
        dl._pick_safe_ip("mixed.example.com")  # one private → reject the whole host


def test_pick_safe_ip_returns_validated_public(monkeypatch):
    monkeypatch.setattr(dl, "_resolve_ips", lambda h: ["93.184.216.34"])
    assert dl._pick_safe_ip("cdn.example.com") == "93.184.216.34"


@pytest.mark.asyncio
async def test_transport_pins_validated_ip_and_keeps_sni(monkeypatch):
    """The connection target is rewritten to the validated IP, while the TLS SNI
    stays the real hostname (Host header is already the hostname)."""
    monkeypatch.setattr(dl, "_resolve_ips", lambda h: ["93.184.216.34"])
    captured = {}

    async def fake_super(self, request):
        captured["host"] = request.url.host
        captured["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_super)
    transport = dl._PublicOnlyTransport()
    try:
        await transport.handle_async_request(httpx.Request("GET", "https://cdn.example.com/x.png"))
    finally:
        await transport.aclose()

    assert captured["host"] == "93.184.216.34"        # connected to validated IP
    assert captured["sni"] == "cdn.example.com"       # TLS still verifies hostname


@pytest.mark.asyncio
async def test_transport_rejects_rebound_private_ip(monkeypatch):
    """If the host (re)resolves to a private IP at connect time, the transport
    refuses to connect — closes the rebinding window."""
    monkeypatch.setattr(dl, "_resolve_ips", lambda h: ["10.0.0.5"])
    connected = False

    async def fake_super(self, request):
        nonlocal connected
        connected = True
        return httpx.Response(200)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", fake_super)
    transport = dl._PublicOnlyTransport()
    try:
        with pytest.raises(ValidationException):
            await transport.handle_async_request(httpx.Request("GET", "https://evil.example.com/x.png"))
    finally:
        await transport.aclose()
    assert connected is False  # never reached the network
