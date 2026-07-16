"""
Integration test for ``StatsRepository._doliv_margin_usdt`` — the долив slice of
the dashboard profit — against a REAL in-memory session.

A долив settle moves the requester's price → system (``SYSTEM_COMMISSION``) and
the доливщик reward system → executor (``TRADER_REWARD``); the residual
``price − reward`` is real platform profit on the system balance. Доливы are
``Payout`` rows, not ``Order``s, so before this they were absent from the
order-based profit aggregation. ``get_order_aggregates`` now folds this slice
into ``profit_usdt`` (the fold itself is unit-tested in
``tests/unit/test_stats_service.py``; the full aggregate can't run on SQLite
because the teamlead sub-query uses Postgres ``split_part``).

Asserts the SQL itself:
  * COMPLETED доливы sum ``Σ(doliv_price_usdt − trader_fee_usdt)`` (incl. a
    0-reward долив);
  * a долив NOT in COMPLETED (CREATED / CLAIMED / AWAITING_CHECK / CANCELED /
    EXPIRED) is excluded → an admin pushing a долив out of COMPLETED via the
    state machine drops its margin from the sum automatically;
  * a non-долив payout (``is_doliv=False``) is excluded even when it carries
    price/reward-shaped columns;
  * the date window filters by ``Payout.created_at``.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.modules.payouts.models import Payout
from app.modules.stats.repository import StatsRepository


async def _mk_doliv(
    session, *, price, reward, status=PayoutStatus.COMPLETED,
    is_doliv=True, created_at=None, completed_at=None,
) -> Payout:
    kwargs = dict(
        uuid=uuid4(), external_id=f"doliv-{uuid4().hex}", is_doliv=is_doliv,
        requester_trader_id=1, refill_requisite_id=None, trader_id=None,
        payment_method=PaymentMethod.SBP, amount=Decimal("0"), currency=Currency.RUB,
        amount_usdt=Decimal("0"), exchange_rate=Decimal("100"),
        doliv_price_usdt=Decimal(price), trader_fee_usdt=Decimal(reward),
        req_holder="Ivan", req_number="40817000", req_extra="Sber",
        status=status,
    )
    if created_at is not None:
        kwargs["created_at"] = created_at
    if completed_at is not None:
        kwargs["completed_at"] = completed_at
    p = Payout(**kwargs)
    session.add(p)
    await session.flush()
    return p


@pytest.mark.asyncio
async def test_completed_doliv_margin_summed(session):
    # price 10 − reward 4 = 6 ; price 5 − reward 0 = 5 → 11 total
    await _mk_doliv(session, price="10", reward="4")
    await _mk_doliv(session, price="5", reward="0")

    margin = await StatsRepository(session)._doliv_margin_usdt()

    assert margin == pytest.approx(11.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        PayoutStatus.CREATED,
        PayoutStatus.CLAIMED,
        PayoutStatus.AWAITING_CHECK,
        PayoutStatus.CANCELED,
        PayoutStatus.EXPIRED,
    ],
)
async def test_non_completed_doliv_excluded(session, status):
    await _mk_doliv(session, price="100", reward="1", status=status)
    margin = await StatsRepository(session)._doliv_margin_usdt()
    assert margin == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_non_doliv_payout_excluded(session):
    # A normal (non-долив) payout must NEVER feed the долив margin, even if it
    # carries price/reward-shaped columns.
    await _mk_doliv(session, price="100", reward="1", is_doliv=False)
    margin = await StatsRepository(session)._doliv_margin_usdt()
    assert margin == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_doliv_margin_respects_date_window(session):
    await _mk_doliv(session, price="10", reward="0", created_at=datetime(2026, 6, 1, 12, 0))
    await _mk_doliv(session, price="7", reward="0", created_at=datetime(2026, 6, 20, 12, 0))

    margin = await StatsRepository(session)._doliv_margin_usdt(
        date_from=datetime(2026, 6, 15), date_to=datetime(2026, 6, 25),
    )
    # Only the June-20 долив falls in the window.
    assert margin == pytest.approx(7.0)


@pytest.mark.asyncio
async def test_sum_doliv_margin_since_filters_by_completed_at(session):
    cutoff = datetime(2026, 6, 20, 0, 0)
    # COMPLETED before cutoff → already baked into the snapshot, excluded.
    await _mk_doliv(session, price="10", reward="2",
                    completed_at=datetime(2026, 6, 19, 12, 0))
    # COMPLETED after cutoff → counted in the real-time delta.
    await _mk_doliv(session, price="9", reward="3",
                    completed_at=datetime(2026, 6, 21, 12, 0))

    delta = await StatsRepository(session).sum_doliv_margin_since(cutoff)

    # Only the post-cutoff долив contributes: 9 − 3 = 6.
    assert delta == pytest.approx(6.0)
