"""Integration tests for the trader-facing finance reads added in
commits 2685f92 (ledger) and 60943fb (stats) — against a real ledger.

  * FinanceService.list_user_ledger_entries — returns only ledger rows that
    touch the caller's own balances AND fall under the trader-visible
    reference types.
  * FinanceService.get_trader_finance_stats — aggregates the trader's SUCCESS
    orders (processed volume + reward profit) within an optional date range.
"""
from datetime import datetime
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
from app.modules.finance.service import FinanceService
from app.modules.orders.models import Order
from app.modules.users.models import User


async def _mk_user(session, *, username, role=UserRole.TRADER) -> User:
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


async def _mk_balance(session, *, user_id, amount="0", btype=BalanceType.WORK) -> Balance:
    b = Balance(
        user_id=user_id,
        is_system=False,
        type=btype,
        currency=Currency.USDT,
        amount=Decimal(amount),
    )
    session.add(b)
    await session.flush()
    return b


def _mk_entry(*, ref_type, ref_id, from_id=None, to_id=None, amount="1") -> LedgerEntry:
    return LedgerEntry(
        from_balance_id=from_id,
        to_balance_id=to_id,
        amount=Decimal(amount),
        currency=Currency.USDT,
        reference_type=ref_type,
        reference_id=ref_id,
        description=None,
    )


async def _mk_order(session, *, trader_id, status, amount_usdt, trader_fee, confirmed_at) -> Order:
    o = Order(
        uuid=uuid4(),
        external_id=f"ext-{uuid4().hex[:8]}",
        merchant_id=1,
        trader_id=trader_id,
        direction=PaymentDirection.PAYIN,
        payment_method=PaymentMethod.SBP,
        amount=Decimal("1000.00"),
        currency=Currency.RUB,
        amount_usdt=Decimal(amount_usdt),
        trader_fee_usdt=Decimal(trader_fee),
        status=status,
        confirmed_at=confirmed_at,
    )
    session.add(o)
    await session.flush()
    return o


# ── list_user_ledger_entries ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_ledger_returns_only_own_balance_and_allowed_types(session):
    trader = await _mk_user(session, username=f"t_{uuid4().hex[:6]}")
    other = await _mk_user(session, username=f"o_{uuid4().hex[:6]}")
    mine = await _mk_balance(session, user_id=trader.id, amount="100")
    theirs = await _mk_balance(session, user_id=other.id, amount="100")

    session.add_all([
        # allowed type + my balance → included
        _mk_entry(ref_type=LedgerReferenceType.ORDER_PAYIN, ref_id="o1", to_id=mine.id, amount="10"),
        _mk_entry(ref_type=LedgerReferenceType.TRADER_REWARD, ref_id="o2", to_id=mine.id, amount="2"),
        # disallowed type (system commission) on my balance → excluded
        _mk_entry(ref_type=LedgerReferenceType.SYSTEM_COMMISSION, ref_id="o3", from_id=mine.id, amount="1"),
        # allowed type but NOT my balance → excluded
        _mk_entry(ref_type=LedgerReferenceType.ORDER_PAYIN, ref_id="o4", to_id=theirs.id, amount="9"),
    ])
    await session.flush()

    service = FinanceService(session)
    rows = await service.list_user_ledger_entries(trader)
    ref_ids = {r.reference_id for r in rows}
    assert ref_ids == {"o1", "o2"}


@pytest.mark.asyncio
async def test_ledger_reference_type_filter(session):
    trader = await _mk_user(session, username=f"t_{uuid4().hex[:6]}")
    mine = await _mk_balance(session, user_id=trader.id, amount="100")
    session.add_all([
        _mk_entry(ref_type=LedgerReferenceType.ORDER_PAYIN, ref_id="p1", to_id=mine.id, amount="10"),
        _mk_entry(ref_type=LedgerReferenceType.TRADER_REWARD, ref_id="r1", to_id=mine.id, amount="2"),
    ])
    await session.flush()

    service = FinanceService(session)
    only_payin = await service.list_user_ledger_entries(
        trader, reference_type=LedgerReferenceType.ORDER_PAYIN
    )
    assert {r.reference_id for r in only_payin} == {"p1"}


@pytest.mark.asyncio
async def test_ledger_disallowed_reference_type_returns_empty(session):
    trader = await _mk_user(session, username=f"t_{uuid4().hex[:6]}")
    mine = await _mk_balance(session, user_id=trader.id, amount="100")
    session.add(_mk_entry(ref_type=LedgerReferenceType.SYSTEM_COMMISSION, ref_id="x", from_id=mine.id))
    await session.flush()

    service = FinanceService(session)
    rows = await service.list_user_ledger_entries(
        trader, reference_type=LedgerReferenceType.SYSTEM_COMMISSION
    )
    assert rows == []


@pytest.mark.asyncio
async def test_ledger_no_balances_returns_empty(session):
    trader = await _mk_user(session, username=f"t_{uuid4().hex[:6]}")
    service = FinanceService(session)
    assert await service.list_user_ledger_entries(trader) == []


# ── get_trader_finance_stats ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_stats_aggregates_success_orders_in_range(session):
    trader = await _mk_user(session, username=f"t_{uuid4().hex[:6]}")
    other = await _mk_user(session, username=f"o_{uuid4().hex[:6]}")

    await _mk_order(session, trader_id=trader.id, status=OrderStatus.SUCCESS,
                    amount_usdt="100", trader_fee="2", confirmed_at=datetime(2026, 5, 10))
    await _mk_order(session, trader_id=trader.id, status=OrderStatus.SUCCESS,
                    amount_usdt="50", trader_fee="1", confirmed_at=datetime(2026, 5, 12))
    # excluded: not SUCCESS
    await _mk_order(session, trader_id=trader.id, status=OrderStatus.FAILED,
                    amount_usdt="999", trader_fee="9", confirmed_at=datetime(2026, 5, 11))
    # excluded: other trader
    await _mk_order(session, trader_id=other.id, status=OrderStatus.SUCCESS,
                    amount_usdt="999", trader_fee="9", confirmed_at=datetime(2026, 5, 11))
    # excluded by date window
    await _mk_order(session, trader_id=trader.id, status=OrderStatus.SUCCESS,
                    amount_usdt="7", trader_fee="7", confirmed_at=datetime(2026, 4, 1))
    await session.flush()

    service = FinanceService(session)
    stats = await service.get_trader_finance_stats(
        trader, date_from=datetime(2026, 5, 1), date_to=datetime(2026, 5, 31),
    )

    assert stats["processed_usdt"] == 150.0
    assert stats["profit_usdt"] == 3.0
    assert len(stats["orders"]) == 2


@pytest.mark.asyncio
async def test_stats_without_date_range_includes_all_success(session):
    trader = await _mk_user(session, username=f"t_{uuid4().hex[:6]}")
    await _mk_order(session, trader_id=trader.id, status=OrderStatus.SUCCESS,
                    amount_usdt="100", trader_fee="2", confirmed_at=datetime(2026, 5, 10))
    await _mk_order(session, trader_id=trader.id, status=OrderStatus.SUCCESS,
                    amount_usdt="7", trader_fee="1", confirmed_at=datetime(2026, 1, 1))
    await session.flush()

    service = FinanceService(session)
    stats = await service.get_trader_finance_stats(trader)
    assert stats["processed_usdt"] == 107.0
    assert stats["profit_usdt"] == 3.0
    assert len(stats["orders"]) == 2
