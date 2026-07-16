"""SSRF-safe downloader for dispute-evidence links.

A merchant supplies a URL; we fetch the file server-side. Because the URL is
attacker-controlled, every fetch is guarded:

  * only ``http``/``https`` schemes;
  * the host must resolve **exclusively** to public addresses — loopback,
    private, link-local (incl. the cloud-metadata ``169.254.169.254``), reserved,
    multicast and unspecified ranges are blocked;
  * redirects are disabled (a 3xx can't bounce us onto an internal host);
  * the body is streamed with a hard size cap;
  * the downloaded bytes are validated against the allowed evidence formats.
"""
import ipaddress
import os
import socket
from typing import List, NamedTuple, Optional
from urllib.parse import unquote, urlparse

import httpx

from app.common.constants.receipts import (
    ALLOWED_EVIDENCE_EXTENSIONS,
    EXTENSION_TO_TYPE,
    MIME_BY_TYPE,
    TYPE_BY_MIME,
)
from app.core.config import get_settings
from app.core.exceptions import ValidationException
from app.modules.receipts.storage import ReceiptStorage

settings = get_settings()

# canonical type -> the extension we name a downloaded file with
_PRIMARY_EXT = {
    "jpeg": "jpg", "png": "png", "webp": "webp",
    "pdf": "pdf", "mp4": "mp4", "mov": "mov",
}


class DownloadedEvidence(NamedTuple):
    content: bytes
    filename: str
    mime: str


def _is_blocked_ip(ip: str) -> bool:
    """True for any non-public address (or anything unparseable)."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True
    return (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


def _resolve_ips(host: str) -> List[str]:
    """All A/AAAA addresses for ``host`` (own seam so tests can stub DNS)."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _pick_safe_ip(host: str) -> str:
    """Resolve ``host`` and return ONE validated public IP, rejecting if any
    resolved address is non-public. Called at connection time so the IP we
    connect to is exactly the IP we validated."""
    try:
        ips = _resolve_ips(host)
    except OSError:
        raise ValidationException("Could not resolve evidence URL host")
    if not ips:
        raise ValidationException("Could not resolve evidence URL host")
    for ip in ips:
        if _is_blocked_ip(ip):
            raise ValidationException("Evidence URL resolves to a non-public address")
    return ips[0]


def _assert_public_url(url: str) -> None:
    """Cheap fail-fast: only http(s), host present, and a first resolve+validate.
    The authoritative anti-rebinding check lives in ``_PublicOnlyTransport`` (it
    pins the validated IP), so this is defence-in-depth + a clean early error."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValidationException("Only http(s) evidence URLs are allowed")
    host = parsed.hostname
    if not host:
        raise ValidationException("Evidence URL has no host")
    _pick_safe_ip(host)


class _PublicOnlyTransport(httpx.AsyncHTTPTransport):
    """Pins DNS to close the resolve-then-reresolve (rebinding) window: resolve
    + validate the host ONCE, then connect to that exact IP while keeping the
    original ``Host`` header and TLS SNI. A malicious DNS can't swap in a private
    address between the guard check and the actual fetch — the connection only
    ever goes to an address we validated."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        ip = _pick_safe_ip(host)
        # Connect to the validated IP; keep TLS SNI / cert verification on the
        # real hostname (the Host header is already the hostname from build time).
        request.url = request.url.copy_with(host=ip)
        ext = dict(request.extensions)
        ext.setdefault("sni_hostname", host)
        request.extensions = ext
        return await super().handle_async_request(request)


def _filename_for(url: str, content_type: Optional[str]) -> str:
    """Best filename for a downloaded file: the URL basename when it carries an
    allowed extension, else one derived from the response content-type."""
    name = os.path.basename(unquote(urlparse(url).path))
    ext = os.path.splitext(name)[1].lstrip(".").lower()
    if ext in ALLOWED_EVIDENCE_EXTENSIONS:
        return name
    ctype = TYPE_BY_MIME.get(content_type or "")
    if ctype:
        return f"evidence.{_PRIMARY_EXT[ctype]}"
    return name or "evidence"


async def _stream_capped(client: httpx.AsyncClient, url: str):
    max_bytes = settings.MAX_EVIDENCE_SIZE_MB * 1024 * 1024
    async with client.stream("GET", url) as resp:
        if resp.status_code != 200:
            raise ValidationException(f"Evidence URL returned HTTP {resp.status_code}")
        buf = bytearray()
        async for chunk in resp.aiter_bytes():
            buf += chunk
            if len(buf) > max_bytes:
                raise ValidationException(
                    f"Evidence file exceeds {settings.MAX_EVIDENCE_SIZE_MB}MB limit"
                )
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        return bytes(buf), ctype


async def fetch_evidence(url: str, *, client: Optional[httpx.AsyncClient] = None) -> DownloadedEvidence:
    """Download + validate a merchant-supplied evidence URL. Raises
    ``ValidationException`` on an unsafe URL, a non-200 / oversize response, or
    bytes that aren't an allowed evidence format."""
    _assert_public_url(url)

    own = client is None
    if client is None:
        timeout = httpx.Timeout(settings.EVIDENCE_DOWNLOAD_TIMEOUT_SECONDS)
        # Redirects off (no rebinding via 3xx) + the DNS-pinning transport
        # (validated IP is the one we connect to).
        client = httpx.AsyncClient(
            timeout=timeout, follow_redirects=False, transport=_PublicOnlyTransport()
        )
    try:
        content, content_type = await _stream_capped(client, url)
    finally:
        if own:
            await client.aclose()

    filename = _filename_for(url, content_type)
    ReceiptStorage.validate_format(content, filename)  # ext + magic-byte gate

    ext = os.path.splitext(filename)[1].lstrip(".").lower()
    mime = content_type if content_type in TYPE_BY_MIME else MIME_BY_TYPE[EXTENSION_TO_TYPE[ext]]
    return DownloadedEvidence(content=content, filename=filename, mime=mime)
