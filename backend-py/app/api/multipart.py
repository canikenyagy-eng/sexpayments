"""Shared transport helpers for multipart dispute-evidence uploads.

Used by every "open dispute" surface (merchant HMAC API, merchant cabinet,
admin) so they read uploaded files and clean URL fields identically.
"""
from typing import List, Optional, Tuple

from fastapi import UploadFile


async def read_attachments(
    attachments: Optional[List[UploadFile]],
) -> List[Tuple[bytes, Optional[str]]]:
    """Read uploaded evidence files into ``(bytes, filename)``, skipping empty
    multipart parts (some clients send a blank file field)."""
    files: List[Tuple[bytes, Optional[str]]] = []
    for f in attachments or []:
        if not f or not f.filename:
            continue
        files.append((await f.read(), f.filename))
    return files


def clean_urls(urls: Optional[List[str]]) -> List[str]:
    """Drop blank/whitespace-only URL form fields."""
    return [u.strip() for u in (urls or []) if u and u.strip()]
