"""Allowed receipt/evidence file formats + magic-byte detection.

Dispute-evidence (uploaded files and links we download) must be a known media
type, checked by BOTH the claimed extension AND the real leading bytes — so a
renamed file (a PDF served as ``.png``) is rejected. mp4/mov are matched at the
"video" family level, since the two containers share the ``ftyp`` box and real
files routinely carry the sibling brand.
"""
from typing import Optional

# Per-order receipt cap (OOM / abuse backstop). Disputes share the order's
# receipts, so this also bounds appeal evidence.
MAX_RECEIPTS_PER_ORDER = 20

# canonical type -> accepted extensions
_EXTENSIONS_BY_TYPE = {
    "jpeg": {"jpg", "jpeg"},
    "png": {"png"},
    "webp": {"webp"},
    "pdf": {"pdf"},
    "mp4": {"mp4"},
    "mov": {"mov"},
}

# extension -> canonical type
EXTENSION_TO_TYPE = {
    ext: t for t, exts in _EXTENSIONS_BY_TYPE.items() for ext in exts
}
ALLOWED_EVIDENCE_EXTENSIONS = frozenset(EXTENSION_TO_TYPE)

# canonical type -> MIME (used to name downloaded files)
MIME_BY_TYPE = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "pdf": "application/pdf",
    "mp4": "video/mp4",
    "mov": "video/quicktime",
}
TYPE_BY_MIME = {mime: t for t, mime in MIME_BY_TYPE.items()}

# canonical type -> family (mp4 & mov are interchangeable video containers)
_FAMILY_BY_TYPE = {
    "jpeg": "jpeg",
    "png": "png",
    "webp": "webp",
    "pdf": "pdf",
    "mp4": "video",
    "mov": "video",
}


def detect_format(content: bytes) -> Optional[str]:
    """Canonical type from the leading magic bytes, or ``None`` if unrecognised."""
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    if content.startswith(b"%PDF"):
        return "pdf"
    if content[4:8] == b"ftyp":
        # mp4 & mov share the ftyp box; QuickTime uses the ``qt`` brand.
        return "mov" if content[8:10] == b"qt" else "mp4"
    if content[4:8] in (b"moov", b"mdat", b"free", b"wide"):
        return "mov"
    return None


def family_of(canonical_type: Optional[str]) -> Optional[str]:
    if canonical_type is None:
        return None
    return _FAMILY_BY_TYPE.get(canonical_type)
