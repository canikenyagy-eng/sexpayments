"""Regression guard: PayoutRepository.lock() has the same FOR-UPDATE identity-map
staleness that the balance fix closes. Callers preload the payout (plain SELECT)
before locking, and with expire_on_commit=False the re-locked SELECT returns the
STALE in-memory status unless the query forces an identity-map refresh — which
defeats the exclusive-claim + double-settle guards in the payout status funnel.
The lock must carry execution_options(populate_existing=True).
"""
import pytest
from unittest.mock import MagicMock

from app.modules.payouts.repository import PayoutRepository


def _capture():
    captured = {}

    async def _execute(stmt):
        captured["stmt"] = stmt
        res = MagicMock()
        res.scalars.return_value.first.return_value = None
        return res

    session = MagicMock()
    session.execute = _execute
    return session, captured


@pytest.mark.asyncio
async def test_payout_lock_refreshes_identity_map():
    session, captured = _capture()
    await PayoutRepository(session).lock(1)
    assert captured["stmt"].get_execution_options().get("populate_existing") is True
