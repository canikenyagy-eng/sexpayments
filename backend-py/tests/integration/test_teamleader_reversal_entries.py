"""
Integration test for TeamleaderService._get_teamlead_reward_entries.

Regression for the reversal-entry bug: `recalculate_rewards` inserts a reversal
LedgerEntry whose `to_balance_id` is the SYSTEM balance and `from_balance_id`
is the teamlead's balance. A query filtering only by `to_balance_id ==
teamlead_balance.id` silently drops reversals, making the "_reversal" logic in
`get_teamlead_stats` / `get_teamlead_links_enriched` dead code and causing
double-counting of recalculated rewards.
"""
from decimal import Decimal

import pytest

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.teamleaders.models import TeamleadLink
from app.modules.teamleaders.service import TeamleaderService
from app.modules.users.models import User


async def _mk_teamlead(session) -> User:
    u = User(
        username="tl_net",
        password=get_password_hash("pass12345"),
        role=UserRole.TEAMLEAD,
        totp_enabled=False,
        is_blocked=False,
        use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_balance(session, *, user_id=None, is_system=False) -> Balance:
    b = Balance(
        user_id=user_id,
        is_system=is_system,
        type=BalanceType.WORK,
        currency=Currency.USDT,
        amount=Decimal("0"),
    )
    session.add(b)
    await session.flush()
    return b


def _mk_entry(*, from_id, to_id, amount, ref_id) -> LedgerEntry:
    return LedgerEntry(
        from_balance_id=from_id,
        to_balance_id=to_id,
        amount=Decimal(amount),
        currency=Currency.USDT,
        reference_type=LedgerReferenceType.TEAMLEAD_REWARD,
        reference_id=ref_id,
        description=None,
    )


@pytest.mark.asyncio
async def test_get_teamlead_stats_subtracts_reversal_and_does_not_double_count(session):
    """
    Scenario replicating a full recalculation cycle for order 100:
      1) original payout  system → teamlead   10 USDT  (ref "100")
      2) reversal         teamlead → system   10 USDT  (ref "100_reversal")
      3) recalc payout    system → teamlead    7 USDT  (ref "100_recalc_1")

    Correct net earned = 7.  With the buggy query (to_balance only) the
    reversal would be missed and the result would be 10 + 7 = 17.
    """
    teamlead = await _mk_teamlead(session)
    tl_balance = await _mk_balance(session, user_id=teamlead.id)
    sys_balance = await _mk_balance(session, is_system=True)

    session.add_all([
        _mk_entry(
            from_id=sys_balance.id,
            to_id=tl_balance.id,
            amount="10.00",
            ref_id="100",
        ),
        _mk_entry(
            from_id=tl_balance.id,
            to_id=sys_balance.id,
            amount="10.00",
            ref_id="100_reversal",
        ),
        _mk_entry(
            from_id=sys_balance.id,
            to_id=tl_balance.id,
            amount="7.00",
            ref_id="100_recalc_1",
        ),
    ])
    await session.flush()

    service = TeamleaderService(session)
    stats = await service.get_teamlead_stats(teamlead.id)

    assert stats["total_earned_usdt"] == Decimal("7.00")
    assert stats["orders_count"] == 1
    assert stats["active_links_count"] == 0


@pytest.mark.asyncio
async def test_get_teamlead_stats_net_zero_when_fully_reversed(session):
    """Reward fully reversed and never recalculated → net 0, orders_count 0.

    orders_count ignores reversal-only references (no payout to attribute)."""
    teamlead = await _mk_teamlead(session)
    tl_balance = await _mk_balance(session, user_id=teamlead.id)
    sys_balance = await _mk_balance(session, is_system=True)

    session.add_all([
        _mk_entry(
            from_id=sys_balance.id, to_id=tl_balance.id,
            amount="5.00", ref_id="200",
        ),
        _mk_entry(
            from_id=tl_balance.id, to_id=sys_balance.id,
            amount="5.00", ref_id="200_reversal",
        ),
    ])
    await session.flush()

    service = TeamleaderService(session)
    stats = await service.get_teamlead_stats(teamlead.id)

    # Net == 0, clamped to 0 (also what buggy code would incidentally produce
    # for this scenario — but the important assertion is the one below).
    assert stats["total_earned_usdt"] == Decimal("0")
    # Reward entry "200" is a real payout so order is counted once.
    assert stats["orders_count"] == 1


@pytest.mark.asyncio
async def test_get_teamlead_links_enriched_subtracts_reversal_income(session):
    """Per-link income must subtract reversals too, not double-count them."""
    teamlead = await _mk_teamlead(session)
    tl_balance = await _mk_balance(session, user_id=teamlead.id)
    sys_balance = await _mk_balance(session, is_system=True)

    # Trader link → trader_id=777. We'll insert an order with trader_id=777.
    link = TeamleadLink(
        teamlead_id=teamlead.id,
        linked_entity_type=UserRole.TRADER,
        linked_entity_id=777,
        fee_percent=Decimal("1.00"),
        is_active=True,
    )
    session.add(link)

    # Create a trader user matching linked_entity_id so FK-less Order.trader_id
    # lookup remains valid via the select in get_teamlead_links_enriched.
    trader = User(
        id=777,
        username="trader_net",
        password=get_password_hash("pass12345"),
        role=UserRole.TRADER,
        totp_enabled=False,
        is_blocked=False,
        use_shared_balance=True,
    )
    session.add(trader)
    await session.flush()

    # Minimal Order row — only fields used by get_teamlead_links_enriched.
    from app.modules.orders.models import Order
    from app.common.enums.orders import OrderStatus
    from app.common.enums.payments import PaymentDirection, PaymentMethod

    order = Order(
        id=500,
        external_id="ext-500",
        merchant_id=1,
        trader_id=777,
        amount=Decimal("1000"),
        amount_usdt=Decimal("100"),
        currency=Currency.USDT,
        status=OrderStatus.SUCCESS,
        direction=PaymentDirection.PAYIN,
        payment_method=PaymentMethod.CARD if hasattr(PaymentMethod, "CARD") else list(PaymentMethod)[0],
    )
    session.add(order)

    session.add_all([
        _mk_entry(
            from_id=sys_balance.id, to_id=tl_balance.id,
            amount="10.00", ref_id="500",
        ),
        _mk_entry(
            from_id=tl_balance.id, to_id=sys_balance.id,
            amount="10.00", ref_id="500_reversal",
        ),
        _mk_entry(
            from_id=sys_balance.id, to_id=tl_balance.id,
            amount="3.00", ref_id="500_recalc_1",
        ),
    ])
    await session.flush()

    service = TeamleaderService(session)
    enriched = await service.get_teamlead_links_enriched(teamlead.id)

    assert len(enriched) == 1
    assert enriched[0]["linked_entity_type"] == UserRole.TRADER
    # Correct: 10 - 10 + 3 = 3.  Buggy (no reversal seen): 10 + 3 = 13.
    assert enriched[0]["income_usdt"] == Decimal("3.00")
