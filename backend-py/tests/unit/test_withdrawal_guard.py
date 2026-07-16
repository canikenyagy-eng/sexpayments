"""Unit tests for the withdrawal approve/reject STATUS-GUARD ordering (#3) and
the system-balance get-or-create RE-CHECK branch (#22).

These complement the integration money-flow tests
(tests/integration/test_withdrawal_idempotency.py): there we assert real ledger
balances; here we isolate, with mocks, that the guard short-circuits BEFORE any
money-moving call. On real Postgres the row is locked by `get_for_update`; on
SQLite that lock is a no-op, so the application-level status re-check is the ONLY
thing standing between a double-click and a double payout — these tests pin that
ordering so a refactor can't accidentally move the `transfer` ahead of the guard.

Existing tests/unit/test_finance_service.py covers `transfer`/`deposit` and the
create-from-None branch of `get_or_create_system_balance`, but NOT the
approve/reject status guard nor the get-or-create RE-CHECK (return-existing)
branch. This file adds those.
"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency, WithdrawalStatus
from app.common.enums.users import UserRole
from app.core.exceptions import ConflictException, NotFoundException
from app.modules.finance.service import FinanceService


@pytest.fixture
def finance_service():
    return FinanceService(AsyncMock())


def _mk_req(status):
    req = MagicMock()
    req.id = 7
    req.status = status
    req.currency = Currency.USDT
    req.amount = Decimal("100")
    req.fee_amount = Decimal("0")
    req.user_role = UserRole.TRADER
    req.user_id = 1
    req.merchant_id = None
    return req


@pytest.mark.parametrize("already", [WithdrawalStatus.SUCCESS, WithdrawalStatus.REJECTED])
@pytest.mark.asyncio
async def test_approve_short_circuits_on_non_pending(finance_service, already):
    """If the locked row is no longer PENDING, approve raises ConflictException
    and never calls transfer / update — proving the guard runs BEFORE money moves."""
    finance_service.withdrawal_repo = MagicMock()
    finance_service.withdrawal_repo.get_for_update = AsyncMock(return_value=_mk_req(already))
    finance_service.withdrawal_repo.update = AsyncMock()
    finance_service.transfer = AsyncMock()
    finance_service.get_or_create_user_balance = AsyncMock()
    finance_service.get_or_create_system_balance = AsyncMock()
    finance_service.audit_log = AsyncMock()

    with pytest.raises(ConflictException, match="Cannot approve request"):
        await finance_service.approve_withdrawal_request(7, admin_id=42)

    finance_service.withdrawal_repo.get_for_update.assert_awaited_once_with(7)
    finance_service.transfer.assert_not_called()
    finance_service.withdrawal_repo.update.assert_not_called()


@pytest.mark.parametrize("already", [WithdrawalStatus.SUCCESS, WithdrawalStatus.REJECTED])
@pytest.mark.asyncio
async def test_reject_short_circuits_on_non_pending(finance_service, already):
    """Mirror guard for reject: non-PENDING → ConflictException, no money move."""
    finance_service.withdrawal_repo = MagicMock()
    finance_service.withdrawal_repo.get_for_update = AsyncMock(return_value=_mk_req(already))
    finance_service.withdrawal_repo.update = AsyncMock()
    finance_service.transfer = AsyncMock()
    finance_service.get_or_create_user_balance = AsyncMock()
    finance_service.audit_log = AsyncMock()

    with pytest.raises(ConflictException, match="Cannot reject request"):
        await finance_service.reject_withdrawal_request(7, admin_id=42, reason="x")

    finance_service.withdrawal_repo.get_for_update.assert_awaited_once_with(7)
    finance_service.transfer.assert_not_called()
    finance_service.withdrawal_repo.update.assert_not_called()


@pytest.mark.asyncio
async def test_approve_missing_request_raises_not_found(finance_service):
    finance_service.withdrawal_repo = MagicMock()
    finance_service.withdrawal_repo.get_for_update = AsyncMock(return_value=None)
    finance_service.transfer = AsyncMock()

    with pytest.raises(NotFoundException):
        await finance_service.approve_withdrawal_request(404, admin_id=42)
    finance_service.transfer.assert_not_called()


@pytest.mark.asyncio
async def test_reject_missing_request_raises_not_found(finance_service):
    finance_service.withdrawal_repo = MagicMock()
    finance_service.withdrawal_repo.get_for_update = AsyncMock(return_value=None)
    finance_service.transfer = AsyncMock()

    with pytest.raises(NotFoundException):
        await finance_service.reject_withdrawal_request(404, admin_id=42, reason="x")
    finance_service.transfer.assert_not_called()


@pytest.mark.asyncio
async def test_system_balance_returns_existing_without_create(finance_service):
    """RE-CHECK branch: when a system balance already exists, get_or_create
    returns it and NEVER calls create (which would split the platform float)."""
    existing = MagicMock()
    finance_service.balance_repo = MagicMock()
    finance_service.balance_repo.get_system_balance = AsyncMock(return_value=existing)
    finance_service.balance_repo.create = AsyncMock()

    got = await finance_service.get_or_create_system_balance(Currency.USDT, BalanceType.WORK)

    assert got is existing
    finance_service.balance_repo.create.assert_not_called()
