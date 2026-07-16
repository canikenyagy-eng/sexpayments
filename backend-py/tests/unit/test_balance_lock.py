"""Regression guard for the FOR UPDATE balance-lock staleness bug.

`transfer()` locks a balance with `get_for_update`, then checks sufficiency and
does a read-modify-write. But callers preload the same Balance via
`get_or_create_*` (plain SELECT) into the session identity map first, and with
`expire_on_commit=False` a re-SELECT of an already-mapped row returns the STALE
in-memory attributes unless the query forces an identity-map refresh. Without
that refresh the DB-level FOR UPDATE lock is defeated (lost update / overdraft /
double-spend). The fix: every FOR UPDATE balance read carries
`execution_options(populate_existing=True)` — the same pattern
`OrderService._reload_order` already documents and uses.
"""
import pytest
from unittest.mock import MagicMock

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency
from app.modules.finance.repository import BalanceRepository


def _capture_session():
    captured = {}

    async def _execute(stmt):
        captured["stmt"] = stmt
        res = MagicMock()
        res.scalar_one_or_none = MagicMock(return_value=None)
        return res

    session = MagicMock()
    session.execute = _execute
    return session, captured


@pytest.mark.asyncio
async def test_get_for_update_refreshes_identity_map():
    session, captured = _capture_session()
    await BalanceRepository(session).get_for_update(1)
    assert captured["stmt"].get_execution_options().get("populate_existing") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args", [
    ("get_user_balance", (1, BalanceType.WORK, Currency.RUB)),
    ("get_merchant_balance", (1, BalanceType.WORK, Currency.RUB)),
    ("get_payout_terminal_balance", (1, BalanceType.WORK, Currency.RUB)),
    ("get_system_balance", (BalanceType.WORK, Currency.RUB)),
])
async def test_locked_getters_refresh_identity_map(method, args):
    session, captured = _capture_session()
    await getattr(BalanceRepository(session), method)(*args, for_update=True)
    assert captured["stmt"].get_execution_options().get("populate_existing") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("method,args", [
    ("get_user_balance", (1, BalanceType.WORK, Currency.RUB)),
    ("get_system_balance", (BalanceType.WORK, Currency.RUB)),
])
async def test_unlocked_reads_do_not_force_refresh(method, args):
    """Non-locked reads must NOT force a refresh (would clobber in-memory state)."""
    session, captured = _capture_session()
    await getattr(BalanceRepository(session), method)(*args, for_update=False)
    assert captured["stmt"].get_execution_options().get("populate_existing") is not True
