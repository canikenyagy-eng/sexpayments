"""Pure-logic unit tests for PayoutService (mocked session — no DB)."""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.modules.payouts.service import DEFAULT_RECEIPT_AUTO, PayoutService


@pytest.fixture
def svc():
    return PayoutService(MagicMock())


def test_q_quantizes_to_four_dp(svc):
    assert svc._q("100") == Decimal("100.0000")
    assert svc._q(Decimal("1.23456")) == Decimal("1.2346")


def test_calc_trader_fee_percent_of_amount(svc):
    trader = SimpleNamespace(payout_fee_percent=Decimal("5"))
    assert svc._calc_trader_fee(trader, Decimal("100")) == Decimal("5.0000")


def test_calc_trader_fee_zero_when_no_trader_or_pct(svc):
    assert svc._calc_trader_fee(None, Decimal("100")) == Decimal("0.0000")
    assert svc._calc_trader_fee(SimpleNamespace(payout_fee_percent=0), Decimal("100")) == Decimal("0.0000")


def test_effective_receipt_auto_falls_back_to_global(svc):
    assert svc._effective_receipt_auto(None) is DEFAULT_RECEIPT_AUTO
    assert svc._effective_receipt_auto(SimpleNamespace(payout_receipt_auto=None)) is DEFAULT_RECEIPT_AUTO


def test_effective_receipt_auto_respects_trader_override(svc):
    assert svc._effective_receipt_auto(SimpleNamespace(payout_receipt_auto=False)) is False
    assert svc._effective_receipt_auto(SimpleNamespace(payout_receipt_auto=True)) is True


def test_ttl_override_clamp_bounds(svc):
    # clamp(value) = max(MIN, min(MAX, value)) — pin the documented bounds.
    lo, hi = svc._TTL_OVERRIDE_MIN, svc._TTL_OVERRIDE_MAX
    assert lo == 1 and hi == 7 * 24 * 60
    clamp = lambda v: max(lo, min(hi, int(v)))
    assert clamp(0) == lo
    assert clamp(10_000_000) == hi
    assert clamp(120) == 120
