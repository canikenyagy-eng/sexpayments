"""Unit tests for the pure slot game logic (game.py)."""
import game


class FakeRandom:
    """Scripted RNG: ``random()`` returns queued floats, ``choice``/``randrange``
    return queued picks. Lets every branch of roll() be driven deterministically.
    """

    def __init__(self, *, randoms=None, choices=None, randranges=None):
        self._randoms = list(randoms or [])
        self._choices = list(choices or [])
        self._randranges = list(randranges or [])

    def random(self):
        return self._randoms.pop(0)

    def choice(self, seq):
        pick = self._choices.pop(0)
        # Allow scripting by index or by value; fall back to first element.
        if isinstance(pick, int):
            return seq[pick]
        return pick

    def randrange(self, n):
        return self._randranges.pop(0)


# ── win_probability ────────────────────────────────────────────────────


def test_win_probability_zero_for_nonpositive_rtp():
    assert game.win_probability(0) == 0.0
    assert game.win_probability(-50) == 0.0
    assert game.win_probability(None) == 0.0


def test_win_probability_matches_formula():
    rtp = 50.0
    expected = (rtp / 100) / game.average_multiplier()
    assert abs(game.win_probability(rtp) - expected) < 1e-12


def test_win_probability_clamped_to_max():
    # A huge RTP would overshoot; probability is capped at 0.95.
    assert game.win_probability(100_000) == 0.95


def test_win_probability_never_exceeds_bounds():
    for rtp in (0, 1, 25, 80, 97, 150, 1000):
        p = game.win_probability(rtp)
        assert 0.0 <= p <= 0.95


# ── non_winning_symbols ────────────────────────────────────────────────


def test_non_winning_symbols_is_never_three_of_a_kind():
    # Even when the RNG keeps picking the same symbol, the loop must break the
    # triple by replacing a reel.
    rng = FakeRandom(
        choices=["seven", "seven", "seven", "bar"],  # 3x same, then a replacement
        randranges=[1],                                # replace the center reel
    )
    symbols = game.non_winning_symbols(rng)
    assert len(symbols) == 3
    assert len(set(symbols)) > 1, symbols


def test_non_winning_symbols_real_rng_many_runs():
    for _ in range(2000):
        symbols = game.non_winning_symbols()
        assert len(symbols) == 3
        assert len(set(symbols)) > 1


# ── roll ───────────────────────────────────────────────────────────────


def test_roll_win_returns_three_of_a_kind_with_multiplier():
    # random() below win_probability → win; choice() picks "seven".
    rng = FakeRandom(randoms=[0.0], choices=["seven"])
    outcome = game.roll(97.0, rng)
    assert outcome.is_win is True
    assert outcome.symbols == ["seven", "seven", "seven"]
    assert outcome.multiplier == game.PAYOUT_MULTIPLIERS["seven"]


def test_roll_loss_returns_non_matching_and_zero_multiplier():
    # random() == 1.0 is never < probability → loss; non_winning_symbols runs.
    rng = FakeRandom(randoms=[1.0], choices=["bar", "cherry", "lemon"])
    outcome = game.roll(97.0, rng)
    assert outcome.is_win is False
    assert outcome.multiplier == 0.0
    assert len(set(outcome.symbols)) > 1


def test_roll_zero_rtp_always_loses():
    # win_probability(0) == 0 → random() (any value >= 0) is never < 0.
    rng = FakeRandom(randoms=[0.0], choices=["star", "seven", "bar"])
    outcome = game.roll(0, rng)
    assert outcome.is_win is False
    assert outcome.multiplier == 0.0


def test_payout_multipliers_cover_all_symbols():
    assert set(game.PAYOUT_MULTIPLIERS) == set(game.SYMBOLS)
