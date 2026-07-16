"""Pure slot-machine game logic — no DB, no framework, no money side-effects.

Extracted from the original ``app/api/v1/endpoints/miniapp.py`` fat controller
so the random/payout math can be reasoned about and unit-tested in isolation.
Settlement (balances, ledger, spin log) lives in ``api.py`` / ``service.py``.

Determinism for tests: pass an explicit ``rng`` (any ``random.Random``-like
object). Production uses ``SystemRandom`` for unpredictability.
"""
from __future__ import annotations

from dataclasses import dataclass
from random import SystemRandom
from typing import List, Sequence, Tuple

# The reels share one symbol set; a spin is a win only on three-of-a-kind.
SYMBOLS: Tuple[str, ...] = ("seven", "bar", "diamond", "star", "cherry", "lemon")

# Payout multiplier applied to the bet when a symbol lines up three times.
PAYOUT_MULTIPLIERS = {
    "seven": 5.0,
    "bar": 3.0,
    "diamond": 2.5,
    "star": 2.0,
    "cherry": 1.5,
    "lemon": 1.2,
}

# Hard ceiling on the per-spin win probability — keeps a misconfigured RTP
# from turning every spin into a guaranteed win.
_MAX_WIN_PROBABILITY = 0.95

_DEFAULT_RNG = SystemRandom()


@dataclass(frozen=True)
class SpinOutcome:
    """Result of a single spin, independent of any balance/settlement."""

    symbols: List[str]      # three reel symbols, left → center → right
    multiplier: float       # payout multiplier (0.0 when not a win)
    is_win: bool


def average_multiplier() -> float:
    """Mean payout multiplier across all symbols — the RTP normaliser."""
    return sum(PAYOUT_MULTIPLIERS.values()) / len(PAYOUT_MULTIPLIERS)


def win_probability(rtp: float) -> float:
    """Per-spin win probability derived from the configured RTP percentage.

    ``RTP = win_probability * average_multiplier`` (in fraction terms), so we
    invert it and clamp to ``[0, _MAX_WIN_PROBABILITY]``. A negative / zero /
    garbage RTP collapses to 0 (never wins) rather than raising.
    """
    rtp_fraction = max(0.0, float(rtp or 0)) / 100
    raw = rtp_fraction / average_multiplier()
    return max(0.0, min(_MAX_WIN_PROBABILITY, raw))


def non_winning_symbols(rng=_DEFAULT_RNG) -> List[str]:
    """Three symbols guaranteed NOT to be a three-of-a-kind (a loss)."""
    symbols = [rng.choice(SYMBOLS) for _ in range(3)]
    while len(set(symbols)) == 1:
        # Replace one reel with a different symbol so it can't read as a win.
        alternatives: Sequence[str] = [s for s in SYMBOLS if s != symbols[0]]
        symbols[rng.randrange(3)] = rng.choice(alternatives)
    return symbols


def roll(rtp: float, rng=_DEFAULT_RNG) -> SpinOutcome:
    """Roll one spin for the given RTP.

    A win yields three identical symbols + that symbol's multiplier; a loss
    yields a non-matching triple + multiplier 0.
    """
    if rng.random() < win_probability(rtp):
        symbol = rng.choice(SYMBOLS)
        return SpinOutcome([symbol, symbol, symbol], PAYOUT_MULTIPLIERS[symbol], True)
    return SpinOutcome(non_winning_symbols(rng), 0.0, False)
