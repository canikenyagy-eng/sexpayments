"""The cascade adapter must never disk-open an evidence path that escapes
UPLOAD_DIR — defence-in-depth against a traversal/absolute path slipping into a
(legacy) dispute.evidence_files row and being exfiltrated to a provider.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.cascading.integrations.mock import MockProviderAdapter
from app.modules.receipts import storage as storage_mod

_PNG = b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_upload_file_skips_path_outside_upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_mod.settings, "UPLOAD_DIR", str(tmp_path / "uploads"), raising=False)
    (tmp_path / "uploads").mkdir()
    outside = tmp_path / "secret.png"           # exists, but OUTSIDE upload dir
    outside.write_bytes(_PNG)

    adapter = MockProviderAdapter()
    adapter.safe_request = AsyncMock(return_value="SENT")

    out = await adapter.upload_file(
        provider=MagicMock(id=1), method="POST", path="/dispute", file_path=str(outside),
    )
    assert out is None
    adapter.safe_request.assert_not_awaited()    # never opened / sent


@pytest.mark.asyncio
async def test_upload_file_allows_path_inside_upload_dir(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(storage_mod.settings, "UPLOAD_DIR", str(upload_dir), raising=False)
    inside = upload_dir / "ok.png"
    inside.write_bytes(_PNG)

    adapter = MockProviderAdapter()
    adapter.safe_request = AsyncMock(return_value="SENT")

    out = await adapter.upload_file(
        provider=MagicMock(id=1), method="POST", path="/dispute", file_path=str(inside),
    )
    assert out == "SENT"
    adapter.safe_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_upload_files_filters_unsafe_paths(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(storage_mod.settings, "UPLOAD_DIR", str(upload_dir), raising=False)
    good = upload_dir / "good.png"
    good.write_bytes(_PNG)
    bad = tmp_path / "passwd"
    bad.write_bytes(b"root:x:0:0")

    captured = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return "SENT"

    adapter = MockProviderAdapter()
    adapter.safe_request = _capture

    await adapter.upload_files(
        provider=MagicMock(id=1), method="POST", path="/dispute",
        file_paths=[str(good), str(bad)],
    )
    # Only the in-upload-dir file made it into the multipart payload.
    sent = captured.get("files") or []
    assert len(sent) == 1
