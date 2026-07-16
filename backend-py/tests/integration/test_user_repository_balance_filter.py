"""
Integration test for UserRepository.get_users balance filter.

Regression for Bug #1: users without a USDT WORK balance row must still be
returned when balance_from/balance_to filters are satisfied by their implicit
balance of 0 (previously dropped by INNER JOIN).
"""
from decimal import Decimal

import pytest

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.finance.models import Balance
from app.modules.users.models import User
from app.modules.users.repository import UserRepository


async def _mk_user(session, username: str, role: UserRole = UserRole.TRADER) -> User:
    u = User(
        username=username,
        password=get_password_hash("pass12345"),
        role=role,
        totp_enabled=False,
        is_blocked=False,
        use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_balance(session, user_id: int, amount: str) -> None:
    session.add(
        Balance(
            user_id=user_id,
            type=BalanceType.WORK,
            currency=Currency.USDT,
            amount=Decimal(amount),
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_get_users_balance_to_includes_users_without_balance_row(session):
    """A user without a balance row has implicit balance=0 and must be included
    when balance_to>=0."""
    no_balance = await _mk_user(session, "no_balance_trader")
    low_balance = await _mk_user(session, "low_balance_trader")
    high_balance = await _mk_user(session, "high_balance_trader")

    await _mk_balance(session, low_balance.id, "50.0")
    await _mk_balance(session, high_balance.id, "10000.0")
    await session.commit()

    repo = UserRepository(session)
    users, total = await repo.get_users(balance_to=1000)

    usernames = {u.username for u in users}
    assert "no_balance_trader" in usernames, "User without balance row must not be dropped"
    assert "low_balance_trader" in usernames
    assert "high_balance_trader" not in usernames
    assert total == 2


@pytest.mark.asyncio
async def test_get_users_balance_from_excludes_users_without_balance_row(session):
    """balance_from>0 must exclude users whose implicit balance is 0."""
    no_balance = await _mk_user(session, "no_balance")
    some_balance = await _mk_user(session, "has_balance")
    await _mk_balance(session, some_balance.id, "500.0")
    await session.commit()

    repo = UserRepository(session)
    users, total = await repo.get_users(balance_from=100)

    usernames = {u.username for u in users}
    assert "no_balance" not in usernames
    assert "has_balance" in usernames
    assert total == 1


@pytest.mark.asyncio
async def test_get_users_balance_range(session):
    """Both bounds together: implicit 0 falls below the lower bound."""
    u_zero = await _mk_user(session, "zero_user")
    u_in = await _mk_user(session, "in_range_user")
    u_high = await _mk_user(session, "high_user")

    await _mk_balance(session, u_in.id, "250.0")
    await _mk_balance(session, u_high.id, "5000.0")
    await session.commit()

    repo = UserRepository(session)
    users, total = await repo.get_users(balance_from=100, balance_to=1000)

    usernames = {u.username for u in users}
    assert usernames == {"in_range_user"}
    assert total == 1
