"""PayoutService._save_receipt must run the same content gate (extension
allowlist + magic-byte match) that every other receipt/evidence upload path
uses, so a renamed HTML/SVG can't be stored as a "receipt" (defence-in-depth
against any future path that serves it back)."""
from types import SimpleNamespace
from uuid import uuid4

import pytest
from unittest.mock import MagicMock

from app.core.exceptions import ValidationException
from app.modules.payouts.service import PayoutService


class _FakeUpload:
    def __init__(self, content: bytes, filename: str):
        self._content = content
        self.filename = filename

    async def read(self) -> bytes:
        return self._content


@pytest.mark.asyncio
@pytest.mark.parametrize("content,filename", [
    (b"<svg onload=alert(1)></svg>", "evil.svg"),        # disallowed extension
    (b"<html><script>alert(1)</script></html>", "x.pdf"),  # magic bytes != pdf
])
async def test_payout_save_receipt_rejects_non_receipt_content(content, filename):
    svc = PayoutService(MagicMock())
    payout = SimpleNamespace(uuid=uuid4())
    with pytest.raises(ValidationException):
        await svc._save_receipt(payout, _FakeUpload(content, filename))


@pytest.mark.asyncio
async def test_payout_save_receipt_accepts_valid_pdf(tmp_path, monkeypatch):
    """Happy path: a real PDF (matching magic bytes + extension) still uploads
    successfully through the new validate_format gate — guards against an
    accidental full-rejection regression on the payout receipt endpoint."""
    from app.core.config import get_settings
    monkeypatch.setattr(get_settings(), "UPLOAD_DIR", str(tmp_path), raising=False)

    svc = PayoutService(MagicMock())
    payout = SimpleNamespace(uuid=uuid4())
    pdf = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF"
    path = await svc._save_receipt(payout, _FakeUpload(pdf, "receipt.pdf"))
    assert path.endswith(".pdf")
    import os
    assert os.path.exists(path)
