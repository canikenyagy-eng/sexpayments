"""
Integration test for WithdrawalRequestRepository.list_all user_login filter.

Background: in the pre-016 schema `WithdrawalRequest.user_id` was polymorphic
(User.id for trader/teamlead, Merchant.id for merchant rows), and filtering
by login had to switch lookup tables per role. Migration 016 normalised this:

  * every withdrawal now stores ``user_id = users.id`` regardless of role;
  * the optional ``merchant_id`` column points at the terminal that funded
    a merchant-owner withdrawal.

These tests verify the **current** invariant: filtering by ``user_login``
goes through the single ``users`` table for all roles, and a Merchant.id
that happens to collide with someone's User.id cannot leak into the
filter — there is no merchant withdrawal row pointing at Merchant.id any
more, so the collision is unreachable.
"""
import uuid as uuid_pkg
from decimal import Decimal

import pytest

from app.common.enums.finances import Currency, WithdrawalStatus
from app.common.enums.merchants import TerminalStatus
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.finance.models import WithdrawalRequest
from app.modules.finance.repository import WithdrawalRequestRepository
from app.modules.merchants.models import Merchant
from app.modules.users.models import User


async def _mk_user(session, username: str, role: UserRole) -> User:
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


async def _mk_merchant(session, owner_user_id: int, name: str) -> Merchant:
    m = Merchant(
        user_id=owner_user_id,
        name=name,
        telegram_user_ids=[],
        status=TerminalStatus.PENDING,
        currency=Currency.RUB,
        api_key=f"key_{name}_{uuid_pkg.uuid4().hex[:8]}",
        api_secret=f"secret_{name}",
        fees={},
    )
    session.add(m)
    await session.flush()
    return m


async def _mk_withdrawal(
    session,
    *,
    user_role: UserRole,
    user_id: int,
    amount: str = "10.0",
    merchant_id: int | None = None,
) -> WithdrawalRequest:
    """Construct a withdrawal row using the **post-016** shape:

      trader/teamlead → user_id = User.id, merchant_id = None
      merchant        → user_id = owner User.id, merchant_id = Merchant.id

    Pre-016 callers stored Merchant.id in user_id; that path is closed
    (see app.modules.finance.service.create_withdrawal_request:376-378).
    """
    wr = WithdrawalRequest(
        user_role=user_role,
        user_id=user_id,
        merchant_id=merchant_id,
        amount=Decimal(amount),
        currency=Currency.USDT,
        destination_address="TEST_ADDR",
        status=WithdrawalStatus.PENDING,
    )
    session.add(wr)
    await session.flush()
    return wr


@pytest.mark.asyncio
async def test_user_login_filter_is_immune_to_user_id_merchant_id_collision(session):
    """Post-016 invariant: even when ``Merchant.id`` happens to equal an
    unrelated ``User.id``, filtering withdrawals by that user's login must
    not surface withdrawals tied to the colliding merchant — because the
    merchant withdrawal points at its owner's ``users.id``, not at
    ``Merchant.id``.

    Setup deliberately constructs the worst case: trader Alice has User.id=N
    AND there is a merchant with Merchant.id=N owned by someone else.
    The filter must return ONLY Alice's withdrawal.
    """
    await _mk_user(session, "filler1", UserRole.TRADER)
    await _mk_user(session, "filler2", UserRole.TRADER)

    alice = await _mk_user(session, "alice", UserRole.TRADER)
    bob_owner = await _mk_user(session, "bobowner", UserRole.MERCHANT)

    # Burn merchant ids until one collides with Alice's user id.
    collider_merchant: Merchant | None = None
    for i in range(20):
        m = await _mk_merchant(session, owner_user_id=bob_owner.id, name=f"mshop{i}")
        if m.id == alice.id:
            collider_merchant = m
            break
    assert collider_merchant is not None, (
        "Could not construct a Merchant.id ≡ User.id collision; check "
        "the insert order — both sequences should now be aligned."
    )

    trader_wr = await _mk_withdrawal(
        session, user_role=UserRole.TRADER, user_id=alice.id, amount="100.0"
    )
    # Correct post-016 shape: user_id = OWNER's User.id, merchant_id = the terminal.
    # The numeric collision (Merchant.id == alice.id) is harmless because
    # the filter never queries the merchants table.
    merchant_wr = await _mk_withdrawal(
        session,
        user_role=UserRole.MERCHANT,
        user_id=bob_owner.id,
        merchant_id=collider_merchant.id,
        amount="200.0",
    )
    await session.commit()

    repo = WithdrawalRequestRepository(session)
    results = await repo.list_all(user_login="alice")

    result_ids = {w.id for w in results}
    assert trader_wr.id in result_ids
    assert merchant_wr.id not in result_ids, (
        "Filter by trader login must not pull in a merchant withdrawal "
        "even when Merchant.id collides with the trader's User.id."
    )


@pytest.mark.asyncio
async def test_user_login_matches_merchant_when_login_is_merchant_owner(session):
    """Merchant withdrawal is owned by ``users[owner].username``. Filtering
    by that login must return the merchant withdrawal (alongside any other
    withdrawals that owner has)."""
    owner = await _mk_user(session, "shopowner", UserRole.MERCHANT)
    shop = await _mk_merchant(session, owner_user_id=owner.id, name="shop1")

    other = await _mk_user(session, "someone_else", UserRole.TRADER)

    merchant_wr = await _mk_withdrawal(
        session,
        user_role=UserRole.MERCHANT,
        user_id=owner.id,
        merchant_id=shop.id,
        amount="50.0",
    )
    other_wr = await _mk_withdrawal(
        session, user_role=UserRole.TRADER, user_id=other.id, amount="9.0"
    )
    await session.commit()

    repo = WithdrawalRequestRepository(session)
    results = await repo.list_all(user_login="shopowner")

    result_ids = {w.id for w in results}
    assert merchant_wr.id in result_ids
    assert other_wr.id not in result_ids


@pytest.mark.asyncio
async def test_user_login_matches_teamlead(session):
    """TEAMLEAD role uses the same ``user_id = users.id`` shape as TRADER."""
    tl = await _mk_user(session, "teamly", UserRole.TEAMLEAD)
    tl_wr = await _mk_withdrawal(
        session, user_role=UserRole.TEAMLEAD, user_id=tl.id, amount="7.0"
    )
    await session.commit()

    repo = WithdrawalRequestRepository(session)
    results = await repo.list_all(user_login="teamly")

    assert {w.id for w in results} == {tl_wr.id}
