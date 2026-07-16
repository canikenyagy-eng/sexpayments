"""Unit tests for ReceiptStorage — the filesystem side of a receipt, now
isolated from the order hot path."""
import os

import pytest

from app.core.exceptions import ValidationException
from app.modules.receipts import storage as storage_mod
from app.modules.receipts.storage import ReceiptStorage


def test_sha256_is_content_hash():
    import hashlib
    assert ReceiptStorage.sha256(b"abc") == hashlib.sha256(b"abc").hexdigest()


def test_validate_size_rejects_oversize(monkeypatch):
    monkeypatch.setattr(storage_mod.settings, "MAX_RECEIPT_SIZE_MB", 1, raising=False)
    ReceiptStorage.validate_size(b"x" * 1024)            # well under 1MB → ok
    with pytest.raises(ValidationException):
        ReceiptStorage.validate_size(b"x" * (1024 * 1024 + 1))   # just over → reject


def test_save_writes_unique_file(tmp_path, monkeypatch):
    import uuid as uuidlib
    monkeypatch.setattr(storage_mod.settings, "UPLOAD_DIR", str(tmp_path), raising=False)
    order_uuid = uuidlib.uuid4()

    p1 = ReceiptStorage.save(order_uuid, b"first", "r.pdf")
    p2 = ReceiptStorage.save(order_uuid, b"second", "r.pdf")

    assert p1 != p2                                  # unique name per save
    assert p1.startswith(str(tmp_path)) and p1.endswith(".pdf")
    assert open(p1, "rb").read() == b"first"
    assert open(p2, "rb").read() == b"second"
    # Both names carry the order uuid prefix.
    assert os.path.basename(p1).startswith(str(order_uuid))


def test_save_handles_missing_filename(tmp_path, monkeypatch):
    import uuid as uuidlib
    monkeypatch.setattr(storage_mod.settings, "UPLOAD_DIR", str(tmp_path), raising=False)
    p = ReceiptStorage.save(uuidlib.uuid4(), b"data", None)
    assert os.path.exists(p)  # no extension, still saved


# ── validate_format: extension allowlist + magic-byte match ─────────────────
# Used to gate dispute-evidence uploads/downloads. Both the claimed extension
# AND the real bytes must agree, so a renamed file (e.g. a PDF as .png) is
# rejected.

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
_JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 8
_WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 8
_PDF = b"%PDF-1.7\n" + b"\x00" * 8
_MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8
_MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 8


@pytest.mark.parametrize("content,filename", [
    (_PNG, "proof.png"),
    (_JPEG, "proof.jpg"),
    (_JPEG, "proof.jpeg"),
    (_WEBP, "proof.webp"),
    (_PDF, "proof.pdf"),
    (_MP4, "clip.mp4"),
    (_MOV, "clip.mov"),
])
def test_validate_format_accepts_allowed(content, filename):
    ReceiptStorage.validate_format(content, filename)  # no raise


def test_validate_format_rejects_disallowed_extension():
    with pytest.raises(ValidationException):
        ReceiptStorage.validate_format(_PNG, "evil.exe")
    with pytest.raises(ValidationException):
        ReceiptStorage.validate_format(_PDF, "doc.docx")


def test_validate_format_rejects_extension_content_mismatch():
    # .png name but real bytes are a PDF → spoof, rejected.
    with pytest.raises(ValidationException):
        ReceiptStorage.validate_format(_PDF, "fake.png")
    # .pdf name but real bytes are PNG.
    with pytest.raises(ValidationException):
        ReceiptStorage.validate_format(_PNG, "fake.pdf")


def test_validate_format_requires_an_extension():
    with pytest.raises(ValidationException):
        ReceiptStorage.validate_format(_PNG, None)
    with pytest.raises(ValidationException):
        ReceiptStorage.validate_format(_PNG, "noext")


def test_validate_format_rejects_unknown_magic_even_with_ok_extension():
    with pytest.raises(ValidationException):
        ReceiptStorage.validate_format(b"not a real image at all", "proof.png")


# ── is_within_upload_dir: containment guard against path traversal ──────────
# Belt-and-suspenders for the cascade forwarders: never disk-open a path that
# escapes UPLOAD_DIR (e.g. a legacy/merchant-controlled "/etc/passwd").


def test_is_within_upload_dir_accepts_paths_under_root(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_mod.settings, "UPLOAD_DIR", str(tmp_path), raising=False)
    inside = str(tmp_path / "abc.png")
    assert ReceiptStorage.is_within_upload_dir(inside) is True


def test_is_within_upload_dir_rejects_outside_and_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(storage_mod.settings, "UPLOAD_DIR", str(tmp_path / "uploads"), raising=False)
    assert ReceiptStorage.is_within_upload_dir("/etc/passwd") is False
    assert ReceiptStorage.is_within_upload_dir(str(tmp_path / "uploads" / ".." / "secret")) is False
    assert ReceiptStorage.is_within_upload_dir("") is False
    assert ReceiptStorage.is_within_upload_dir(None) is False
