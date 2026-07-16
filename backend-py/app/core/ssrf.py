"""SSRF guard for server-side outbound requests to attacker-controlled URLs.

Used by outbound webhook/callback delivery (order + payout callbacks), whose
target URL is merchant-controlled. Blocks non-http(s) schemes and any host that
resolves to a non-public address (loopback, private, link-local incl. the cloud
metadata 169.254.169.254, reserved, multicast, unspecified), and pins DNS at
connect time so a rebind can't bounce the request onto an internal host.

The dispute-evidence downloader has its own equivalent guard in
``app/modules/receipts/download.py`` (kept separate to avoid destabilising its
tested DNS-stub seam); this module is the home for new outbound-URL callers.
"""
import ipaddress
import socket
from typing import List
from urllib.parse import urlparse

import httpx


class SsrfError(Exception):
    """Raised when an outbound target URL is unsafe (bad scheme / non-public)."""


def is_blocked_ip(ip: str) -> bool:
    """True for any non-public address (or anything unparseable)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


def resolve_ips(host: str) -> List[str]:
    """All A/AAAA addresses for ``host`` (own seam so tests can stub DNS)."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def pick_safe_ip(host: str) -> str:
    """Resolve ``host`` and return ONE validated public IP, rejecting the whole
    host if ANY resolved address is non-public. Called at connect time so the IP
    we connect to is exactly the IP we validated."""
    try:
        ips = resolve_ips(host)
    except OSError:
        raise SsrfError("Could not resolve callback URL host")
    if not ips:
        raise SsrfError("Could not resolve callback URL host")
    for ip in ips:
        if is_blocked_ip(ip):
            raise SsrfError("Callback URL resolves to a non-public address")
    return ips[0]


def assert_public_url(url: str) -> None:
    """Fail-fast: only http(s), a host present, resolving exclusively to public
    IPs. The authoritative anti-rebinding check lives in ``PublicOnlyTransport``
    (it pins the validated IP); this is a clean early error + defence in depth."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SsrfError("Only http(s) callback URLs are allowed")
    host = parsed.hostname
    if not host:
        raise SsrfError("Callback URL has no host")
    pick_safe_ip(host)


class PublicOnlyTransport(httpx.AsyncHTTPTransport):
    """Pins DNS to close the resolve-then-reresolve (rebinding) window: resolve +
    validate the host ONCE, then connect to that exact IP while keeping the
    original ``Host`` header and TLS SNI."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        ip = pick_safe_ip(host)
        request.url = request.url.copy_with(host=ip)
        ext = dict(request.extensions)
        ext.setdefault("sni_hostname", host)
        request.extensions = ext
        return await super().handle_async_request(request)
