"""Integration tests for RequisiteLimit turnover counters.

``increment_turnover_by_order`` is applied when an order settles; the new
``decrement_turnover_by_order`` reverses it when a previously-SUCCESS order is
pulled into a dispute. They must be exact mirrors, and the decrement must
never drive a counter below zero (daily/monthly rollover safety).
"""
from decimal import Decimal

import pytest

from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.modules.orders.models import Order
from app.modules.requisites.models import RequisiteLimit
from app.modules.requisites.repository import RequisiteLimitRepository


async def _mk_limit(session, *, requisite_id, daily="0.00", monthly="0.00") -> RequisiteLimit:
    rl = RequisiteLimit(
        requisite_id=requisite_id,
        current_daily_turnover=Decimal(daily),
        current_monthly_turnover=Decimal(monthly),
    )
    session.add(rl)
    await session.flush()
    return rl


async def _mk_order(session, *, requisite_id, amount) -> Order:
    o = Order(
        external_id=f"ext-{requisite_id}",
        merchant_id=1,
        trader_id=1,
        requisite_id=requisite_id,
        direction=PaymentDirection.PAYIN,
        payment_method=PaymentMethod.SBP,
        amount=Decimal(amount),
        currency=Currency.RUB,
        amount_usdt=Decimal("10"),
        status=OrderStatus.SUCCESS,
    )
    session.add(o)
    await session.flush()
    return o


async def _counters(session, requisite_id):
    rl = await RequisiteLimitRepository(session).get_by_requisite_id(requisite_id)
    return rl.current_daily_turnover, rl.current_monthly_turnover


@pytest.mark.asyncio
async def test_increment_then_decrement_is_symmetric(session):
    await _mk_limit(session, requisite_id=1, daily="500.00", monthly="2000.00")
    order = await _mk_order(session, requisite_id=1, amount="1000.00")
    repo = RequisiteLimitRepository(session)

    await repo.increment_turnover_by_order(order)
    daily, monthly = await _counters(session, 1)
    assert daily == Decimal("1500.00")
    assert monthly == Decimal("3000.00")

    await repo.decrement_turnover_by_order(order)
    daily, monthly = await _counters(session, 1)
    assert daily == Decimal("500.00")
    assert monthly == Decimal("2000.00")


@pytest.mark.asyncio
async def test_decrement_clamps_at_zero(session):
    """If counters were reset (rollover) after settlement, the dispute-time
    decrement must not push them negative."""
    await _mk_limit(session, requisite_id=2, daily="0.00", monthly="0.00")
    order = await _mk_order(session, requisite_id=2, amount="1000.00")
    repo = RequisiteLimitRepository(session)

    await repo.decrement_turnover_by_order(order)
    daily, monthly = await _counters(session, 2)
    assert daily == Decimal("0")
    assert monthly == Decimal("0")


@pytest.mark.asyncio
async def test_turnover_noop_when_order_has_no_requisite(session):
    """Orders not tied to a requisite (e.g. cascade) simply skip the counter."""
    order = await _mk_order(session, requisite_id=None, amount="1000.00")
    repo = RequisiteLimitRepository(session)
    # Must not raise even though there's no RequisiteLimit row.
    await repo.increment_turnover_by_order(order)
    await repo.decrement_turnover_by_order(order)
