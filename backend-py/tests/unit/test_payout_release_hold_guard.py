"""Pure-logic unit tests for ``PayoutService.release_hold``'s defensive
early-out guard (mocked session — no DB).

The guard is money-critical: ``release_hold`` moves the trader's held earnings
ESCROW → WORK, so it MUST bail out *before* locking the row / calling
FinanceService whenever there is nothing legitimate to release:
  * ``trader_hold_until`` is None  → there was never a hold (no parked ESCROW).
  * ``hold_released_at`` is set     → the hold was ALREADY released (re-run / retry).

If the guard ever regressed, the service would try to drain an empty (or
already-drained) trader ESCROW into WORK — fabricating money. These tests pin
that the guard short-circuits without touching the repository lock or finance,
which the in-memory integration suite cannot prove (it asserts the *net* effect,
not that the early-out fired before the lock).
"""
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.common.types import utcnow
from app.modules.payouts.service import PayoutService

PAST = utcnow() - timedelta(hours=2)


@pytest.fixture
def svc():
    s = PayoutService(MagicMock())
    # Trip-wires: the guard must return BEFORE any of these run.
    s.repository = MagicMock()
    s.repository.lock = AsyncMock(side_effect=AssertionError("must not lock the payout"))
    s.finance = MagicMock()
    s.finance.release_payout_hold = AsyncMock(side_effect=AssertionError("must not move money"))
    return s


@pytest.mark.asyncio
async def test_release_hold_noops_when_no_hold_was_set(svc):
    """trader_hold_until is None → there is nothing parked; return immediately."""
    payout = SimpleNamespace(id=1, trader_hold_until=None, hold_released_at=None)
    await svc.release_hold(payout)        # no AssertionError → guard fired
    svc.repository.lock.assert_not_called()
    svc.finance.release_payout_hold.assert_not_called()


@pytest.mark.asyncio
async def test_release_hold_noops_when_already_released(svc):
    """hold_released_at already set → a retry / second worker run must NOT
    re-release (the idempotency guard for the held-earnings double-spend)."""
    payout = SimpleNamespace(id=2, trader_hold_until=PAST, hold_released_at=utcnow())
    await svc.release_hold(payout)        # no AssertionError → guard fired
    svc.repository.lock.assert_not_called()
    svc.finance.release_payout_hold.assert_not_called()


@pytest.mark.asyncio
async def test_release_hold_noops_when_neither_field_present(svc):
    """Both falsy (a non-hold payout) → no-op."""
    payout = SimpleNamespace(id=3, trader_hold_until=None, hold_released_at=None)
    await svc.release_hold(payout)
    svc.repository.lock.assert_not_called()
    svc.finance.release_payout_hold.assert_not_called()
