"""Integration tests for OrderRepository.count_orders — the total that powers
the ``X-Total-Count`` pagination header. Pins that count uses the SAME filters
as list_orders and is independent of skip/limit.
"""
from decimal import Decimal
from uuid import uuid4

import pytest

from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.orders.repository import OrderRepository
from app.modules.users.models import User


async def _mk_merchant(session) -> Merchant:
    u = User(
        username=f"mu_{uuid4().hex[:6]}", password=get_password_hash("pass12345"),
        role=UserRole.MERCHANT, totp_enabled=False, is_blocked=False, use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    m = Merchant(
        user_id=u.id, name=f"M-{uuid4().hex[:5]}", status=TerminalStatus.ENABLED,
        currency=Currency.RUB, api_key=f"k-{uuid4().hex[:6]}", api_secret=f"s-{uuid4().hex[:6]}",
    )
    session.add(m)
    await session.flush()
    return m


async def _mk_order(session, merchant_id, *, status, method=PaymentMethod.SBP) -> Order:
    o = Order(
        uuid=uuid4(), external_id=f"ext-{uuid4().hex[:8]}", merchant_id=merchant_id,
        direction=PaymentDirection.PAYIN, payment_method=method,
        amount=Decimal("1000.00"), currency=Currency.RUB, amount_usdt=Decimal("100"),
        status=status,
    )
    session.add(o)
    await session.flush()
    return o


@pytest.mark.asyncio
async def test_count_matches_filtered_total_and_ignores_pagination(session):
    merchant = await _mk_merchant(session)
    # 5 SUCCESS + 3 FAILED for this merchant.
    for _ in range(5):
        await _mk_order(session, merchant.id, status=OrderStatus.SUCCESS)
    for _ in range(3):
        await _mk_order(session, merchant.id, status=OrderStatus.FAILED)

    repo = OrderRepository(session)

    # Total for the merchant, regardless of page window.
    assert await repo.count_orders(merchant_ids=merchant.id) == 8
    # Status filter mirrors list_orders.
    assert await repo.count_orders(merchant_ids=merchant.id, statuses=[OrderStatus.SUCCESS]) == 5
    # Pagination args don't apply to count — a small page still reports the full total.
    page = await repo.list_orders(merchant_ids=merchant.id, skip=0, limit=2)
    assert len(page) == 2
    assert await repo.count_orders(merchant_ids=merchant.id) == 8


@pytest.mark.asyncio
async def test_count_zero_for_empty_merchant_scope(session):
    repo = OrderRepository(session)
    # Empty merchant-id sequence → hard "no rows" short-circuit (count 0, list []).
    assert await repo.count_orders(merchant_ids=[]) == 0
    assert await repo.list_orders(merchant_ids=[]) == []


@pytest.mark.asyncio
async def test_count_is_merchant_scoped(session):
    m1 = await _mk_merchant(session)
    m2 = await _mk_merchant(session)
    await _mk_order(session, m1.id, status=OrderStatus.SUCCESS)
    await _mk_order(session, m1.id, status=OrderStatus.SUCCESS)
    await _mk_order(session, m2.id, status=OrderStatus.SUCCESS)

    repo = OrderRepository(session)
    assert await repo.count_orders(merchant_ids=m1.id) == 2
    assert await repo.count_orders(merchant_ids=m2.id) == 1
