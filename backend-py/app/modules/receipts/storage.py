"""Receipt file storage — the filesystem side of a receipt, isolated from the
order hot path so it's testable on its own and swappable (local disk today;
S3/object-store later) without touching ``confirm_order``.

Stateless: the upload directory + size limit come from settings.
"""
import hashlib
import os
import uuid as uuidlib
from pathlib import Path
from typing import Optional

from app.core.config import get_settings
from app.core.exceptions import ValidationException

settings = get_settings()


class ReceiptStorage:
    @staticmethod
    def sha256(content: bytes) -> str:
        """Content hash — the dedup key AND the fraud-check key."""
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def validate_size(content: bytes) -> None:
        max_bytes = settings.MAX_RECEIPT_SIZE_MB * 1024 * 1024
        if len(content) > max_bytes:
            raise ValidationException(
                f"File size exceeds {settings.MAX_RECEIPT_SIZE_MB}MB limit"
            )

    @staticmethod
    def validate_format(content: bytes, filename: Optional[str]) -> None:
        """Reject anything that isn't an allowed evidence format. Requires a
        recognised extension AND leading bytes whose media family matches it
        (so a renamed file — e.g. a PDF as ``.png`` — is rejected). Used to gate
        dispute-evidence uploads and downloads."""
        from app.common.constants.receipts import (
            ALLOWED_EVIDENCE_EXTENSIONS,
            EXTENSION_TO_TYPE,
            detect_format,
            family_of,
        )

        ext = os.path.splitext(filename or "")[1].lstrip(".").lower()
        if ext not in ALLOWED_EVIDENCE_EXTENSIONS:
            allowed = ", ".join(sorted(ALLOWED_EVIDENCE_EXTENSIONS))
            raise ValidationException(
                f"Unsupported file format '{ext or filename}'. Allowed: {allowed}"
            )

        detected = detect_format(content)
        if detected is None or family_of(detected) != family_of(EXTENSION_TO_TYPE[ext]):
            raise ValidationException(
                "File content does not match its extension"
            )

    @staticmethod
    def is_within_upload_dir(file_path: Optional[str]) -> bool:
        """True only if ``file_path`` resolves inside ``UPLOAD_DIR``. Used as a
        containment guard before disk-opening a stored path (defends the cascade
        forwarders against a traversal/absolute path in legacy evidence rows)."""
        if not file_path:
            return False
        try:
            root = Path(settings.UPLOAD_DIR).resolve()
            target = Path(file_path).resolve()
        except (OSError, ValueError, RuntimeError):
            return False
        return target == root or target.is_relative_to(root)

    @staticmethod
    def save(order_uuid, content: bytes, filename: Optional[str]) -> str:
        """Write the receipt under a unique name (never overwrites an earlier
        receipt for the same order) and return its path."""
        os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
        ext = os.path.splitext(filename)[1] if filename else ""
        name = f"{order_uuid}_{uuidlib.uuid4().hex[:8]}{ext}"
        path = os.path.join(settings.UPLOAD_DIR, name)
        with open(path, "wb") as f:
            f.write(content)
        return path
