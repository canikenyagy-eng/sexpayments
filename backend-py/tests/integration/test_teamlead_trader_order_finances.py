"""Integration tests for TeamleaderService.get_teamlead_trader_order_finances
(added in commit f35e044) — against a real ledger.

The method lists a teamlead's SUCCESS orders sourced from *linked trader*
accounts, with the net teamlead reward per order (payouts minus reversals),
derived from the "Trader Teamlead Reward" ledger entries.
"""
from decimal import Decimal
from uuid import uuid4

import pytest

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.orders.models import Order
from app.modules.teamleaders.models import TeamleadLink
from app.modules.teamleaders.service import TeamleaderService
from app.modules.users.models import User


async def _mk_user(session, *, username, role) -> User:
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


async def _mk_link(session, *, teamlead_id, trader_id, is_active=True) -> TeamleadLink:
    link = TeamleadLink(
        teamlead_id=teamlead_id,
        linked_entity_type=UserRole.TRADER,
        linked_entity_id=trader_id,
        fee_percent=Decimal("1.00"),
        is_active=is_active,
    )
    session.add(link)
    await session.flush()
    return link


async def _mk_order(session, *, order_id, trader_id, status=OrderStatus.SUCCESS) -> Order:
    o = Order(
        id=order_id,
        uuid=uuid4(),
        external_id=f"ext-{order_id}",
        merchant_id=1,
        trader_id=trader_id,
        direction=PaymentDirection.PAYIN,
        payment_method=PaymentMethod.SBP,
        amount=Decimal("1000.00"),
        currency=Currency.RUB,
        amount_usdt=Decimal("100"),
        status=status,
    )
    session.add(o)
    await session.flush()
    return o


def _reward(*, from_id, to_id, amount, ref_id, desc="Trader Teamlead Reward") -> LedgerEntry:
    return LedgerEntry(
        from_balance_id=from_id,
        to_balance_id=to_id,
        amount=Decimal(amount),
        currency=Currency.USDT,
        reference_type=LedgerReferenceType.TEAMLEAD_REWARD,
        reference_id=ref_id,
        description=desc,
    )


async def _setup_teamlead_with_trader(session):
    tl = await _mk_user(session, username=f"tl_{uuid4().hex[:6]}", role=UserRole.TEAMLEAD)
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}", role=UserRole.TRADER)
    tl_bal = await _mk_balance(session, user_id=tl.id)
    sys_bal = await _mk_balance(session, is_system=True)
    return tl, trader, tl_bal, sys_bal


@pytest.mark.asyncio
async def test_lists_order_with_net_reward(session):
    tl, trader, tl_bal, sys_bal = await _setup_teamlead_with_trader(session)
    await _mk_link(session, teamlead_id=tl.id, trader_id=trader.id)
    order = await _mk_order(session, order_id=501, trader_id=trader.id)
    session.add(_reward(from_id=sys_bal.id, to_id=tl_bal.id, amount="10.00", ref_id=str(order.id)))
    await session.flush()

    service = TeamleaderService(session)
    rows = await service.get_teamlead_trader_order_finances(tl.id)

    assert len(rows) == 1
    row = rows[0]
    assert row["order_id"] == 501
    assert row["teamlead_profit_usdt"] == Decimal("10.00")
    assert row["trader_id"] == trader.id
    assert row["trader_login"] == trader.username
    assert row["amount_usdt"] == Decimal("100")


@pytest.mark.asyncio
async def test_fully_reversed_reward_is_excluded(session):
    tl, trader, tl_bal, sys_bal = await _setup_teamlead_with_trader(session)
    await _mk_link(session, teamlead_id=tl.id, trader_id=trader.id)
    order = await _mk_order(session, order_id=502, trader_id=trader.id)
    session.add_all([
        _reward(from_id=sys_bal.id, to_id=tl_bal.id, amount="10.00", ref_id="502"),
        _reward(from_id=tl_bal.id, to_id=sys_bal.id, amount="10.00", ref_id="502_reversal"),
    ])
    await session.flush()

    service = TeamleaderService(session)
    rows = await service.get_teamlead_trader_order_finances(tl.id)
    # Net reward is 0 → the row is filtered out (reward <= 0).
    assert rows == []


@pytest.mark.asyncio
async def test_merchant_reward_is_not_counted(session):
    tl, trader, tl_bal, sys_bal = await _setup_teamlead_with_trader(session)
    await _mk_link(session, teamlead_id=tl.id, trader_id=trader.id)
    order = await _mk_order(session, order_id=503, trader_id=trader.id)
    # Reward tagged as a MERCHANT teamlead reward → must be ignored.
    session.add(_reward(
        from_id=sys_bal.id, to_id=tl_bal.id, amount="10.00",
        ref_id="503", desc="Merchant Teamlead Reward",
    ))
    await session.flush()

    service = TeamleaderService(session)
    rows = await service.get_teamlead_trader_order_finances(tl.id)
    assert rows == []


@pytest.mark.asyncio
async def test_inactive_link_yields_no_rows(session):
    tl, trader, tl_bal, sys_bal = await _setup_teamlead_with_trader(session)
    await _mk_link(session, teamlead_id=tl.id, trader_id=trader.id, is_active=False)
    order = await _mk_order(session, order_id=504, trader_id=trader.id)
    session.add(_reward(from_id=sys_bal.id, to_id=tl_bal.id, amount="10.00", ref_id="504"))
    await session.flush()

    service = TeamleaderService(session)
    rows = await service.get_teamlead_trader_order_finances(tl.id)
    assert rows == []
