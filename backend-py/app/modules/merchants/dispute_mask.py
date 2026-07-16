"""Per-merchant appeal-message id extraction (the ``dispute_id_mask`` field).

Merchants format their dispute/appeal messages differently — OUR order id may
not be the first token (the merchant's own internal id often is). An admin
configures a token-position mask per merchant; the merchant-dispute-bot forwards
the full message text and we pull out the candidate id(s), then resolve them
against our DB.

Mask grammar (case-insensitive, ``N`` is 1-based):
    "N"        → the N-th whitespace token
    "word:N"   → same, explicit
    "uuid:N"   → the N-th UUID-shaped token

The mask is only a *priority hint*: the caller resolves candidates against the
DB (uuid → external_id), so a wrong-but-present token (e.g. the merchant's own
id, which isn't in our system) simply won't match. That keeps a slightly-off
mask from breaking intake — we fall back to scanning every token.

Pure functions only — no DB, no I/O — so the extraction is unit-testable in
isolation from the HTTP/DB layers.
"""
from __future__ import annotations

import re
import uuid as _uuid
from typing import List, Optional

# Defensive bounds so a pathological message can't blow up CPU/memory.
_MAX_TEXT = 8192
_MAX_TOKENS = 512

# "N" | "word:N" | "uuid:N"  (N up to 3 digits)
_MASK_RE = re.compile(r"^\s*(?:(word|uuid)\s*:\s*)?(\d{1,3})\s*$", re.IGNORECASE)


def is_uuid(token: str) -> bool:
    try:
        _uuid.UUID(token)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _tokens(text: str) -> List[str]:
    return text[:_MAX_TEXT].split()[:_MAX_TOKENS]


def _parse_mask(mask: Optional[str]) -> Optional[tuple[str, int]]:
    """Parse a mask into ``(mode, n)``, or ``None`` if it isn't a valid spec
    (wrong type / unparseable / non-positive index)."""
    if not isinstance(mask, str):
        return None
    m = _MASK_RE.match(mask)
    if not m:
        return None
    n = int(m.group(2))
    if n < 1:
        return None
    return (m.group(1) or "word").lower(), n


def apply_mask(text: Optional[str], mask: Optional[str]) -> Optional[str]:
    """The single token the mask points at, or ``None`` when the mask is
    empty/unparseable or the position is out of range."""
    if not isinstance(text, str) or not text:
        return None
    parsed = _parse_mask(mask)
    if parsed is None:
        return None
    mode, n = parsed
    toks = _tokens(text)
    if mode == "uuid":
        toks = [t for t in toks if is_uuid(t)]
    return toks[n - 1] if n <= len(toks) else None


def candidate_identifiers(
    *, text: Optional[str], identifier: Optional[str], mask: Optional[str]
) -> List[str]:
    """The id candidate(s) to resolve against our DB — at most one, no scanning.

    - A **valid mask is authoritative**: we take only the token it points at. If
      that position yields nothing, there are no candidates (→ 404, surfacing a
      format/mask mismatch rather than silently grabbing some other id). The
      legacy ``identifier`` is ignored while a mask is active.
    - With **no (valid) mask**: the single ``identifier`` the bot pre-extracted
      (e.g. the first uuid). We do NOT scan the rest of the text — a merchant
      whose id isn't that obvious token must configure a mask.
    """
    # Normalise to str|None — HTTP gives str|None, but a direct caller (tests)
    # may pass a FastAPI ``Form`` default object or a mock.
    text = text if isinstance(text, str) else None
    identifier = identifier if isinstance(identifier, str) else None

    # Authoritative mask → only the token it selects.
    if _parse_mask(mask) is not None:
        masked = apply_mask(text, mask)
        masked = masked.strip() if isinstance(masked, str) else ""
        return [masked] if masked else []

    # No mask → the bot's single pre-extracted identifier only (no token scan).
    ident = identifier.strip() if identifier else ""
    return [ident] if ident else []
