"""Unit tests for the per-merchant appeal-id mask (``dispute_id_mask``).

Pure-function coverage for ``apply_mask`` / ``candidate_identifiers`` — the
token-position extraction that pulls OUR order id out of a merchant's free-form
appeal message. No DB / HTTP here; resolution against the DB is the endpoint's
job (these only decide WHICH tokens to try and in what order).
"""
from __future__ import annotations

import pytest

from app.modules.merchants.dispute_mask import apply_mask, candidate_identifiers

OUR = "11111111-1111-4111-8111-111111111111"
THEIRS = "99999999-9999-4999-8999-999999999999"


# ── apply_mask ───────────────────────────────────────────────────────────────

def test_bare_n_picks_nth_word_1_based():
    assert apply_mask("a b c d", "1") == "a"
    assert apply_mask("a b c d", "4") == "d"


def test_word_prefix_is_same_as_bare():
    assert apply_mask("a b c d", "word:3") == "c"
    assert apply_mask("a b c d", "WORD: 2") == "b"  # case-insensitive + spaces


def test_uuid_mode_skips_non_uuid_tokens():
    # "их uuid идёт первым, наш — второй uuid" — even though words between.
    text = f"appeal {THEIRS} for order please {OUR} thanks"
    assert apply_mask(text, "uuid:2") == OUR
    assert apply_mask(text, "uuid:1") == THEIRS


def test_user_scenario_their_uuid_then_ours_3_words_later():
    # «первый uuid их, через 3 слова идёт наш uuid»
    text = f"{THEIRS} оплата по заявке {OUR}"
    assert apply_mask(text, "5") == OUR        # 5th word
    assert apply_mask(text, "uuid:2") == OUR   # or: 2nd uuid


def test_out_of_range_returns_none():
    assert apply_mask("a b", "5") is None
    assert apply_mask(f"{THEIRS}", "uuid:2") is None


def test_empty_or_invalid_mask_returns_none():
    assert apply_mask("a b c", None) is None
    assert apply_mask("a b c", "") is None
    assert apply_mask("a b c", "abc") is None
    assert apply_mask("a b c", "0") is None      # 1-based, 0 invalid
    assert apply_mask("a b c", "-1") is None
    assert apply_mask(None, "1") is None


# ── candidate_identifiers ────────────────────────────────────────────────────

def test_valid_mask_is_authoritative_only_masked_token():
    text = f"{THEIRS} order {OUR}"
    # uuid:2 → OUR; with a mask set we use ONLY that token, THEIRS is not tried.
    cands = candidate_identifiers(text=text, identifier=None, mask="uuid:2")
    assert cands == [OUR]


def test_mask_ignores_legacy_identifier():
    text = f"{THEIRS} order {OUR}"
    # The bot's legacy guess (first uuid = THEIRS) must be ignored when a mask
    # is configured — otherwise the mask wouldn't actually disambiguate.
    cands = candidate_identifiers(text=text, identifier=THEIRS, mask="uuid:2")
    assert cands == [OUR]


def test_legacy_identifier_only_no_text():
    # Old bot path: a single pre-extracted token, no full text / mask.
    cands = candidate_identifiers(text=None, identifier=OUR, mask=None)
    assert cands == [OUR]


def test_no_mask_uses_only_the_identifier():
    # No mask → the single pre-extracted identifier; the text is NOT scanned.
    cands = candidate_identifiers(text=f"{THEIRS} {OUR}", identifier=OUR, mask=None)
    assert cands == [OUR]


def test_no_mask_no_identifier_yields_nothing():
    # No mask and no identifier → nothing (we don't scan the text for tokens).
    cands = candidate_identifiers(text=f"{THEIRS} {OUR}", identifier=None, mask=None)
    assert cands == []


def test_invalid_format_mask_treated_as_no_mask():
    # An unparseable mask isn't a real spec → no-mask path → use the identifier,
    # don't scan the text.
    cands = candidate_identifiers(text=f"{THEIRS} {OUR}", identifier=OUR, mask="abc")
    assert cands == [OUR]


def test_out_of_range_valid_mask_yields_no_candidates():
    # A VALID mask pointing past the end is authoritative → no candidates (404),
    # rather than silently grabbing some other token.
    text = f"{THEIRS} {OUR}"
    cands = candidate_identifiers(text=text, identifier=None, mask="uuid:9")
    assert cands == []


def test_empty_everything_yields_empty_list():
    assert candidate_identifiers(text=None, identifier=None, mask="1") == []
    assert candidate_identifiers(text="   ", identifier="  ", mask=None) == []


def test_non_uuid_external_id_supported_via_word_mask():
    # external_id is NOT always a uuid — word mask must handle arbitrary tokens.
    text = "appeal MERCH-ORDER-7788 attached"
    assert apply_mask(text, "2") == "MERCH-ORDER-7788"
    cands = candidate_identifiers(text=text, identifier=None, mask="2")
    assert cands[0] == "MERCH-ORDER-7788"
