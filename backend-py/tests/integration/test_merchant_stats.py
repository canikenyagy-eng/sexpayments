"""Integration tests for the admin «Мерчанты» stats aggregate (real in-memory
Postgres session). ClickHouse is disabled in tests, so the request counts come
back empty (fail-safe) and we assert the Postgres funnel — created / success /
RUB check-size buckets — plus the ratio maths end-to-end.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.receipt_moderations import ModerationStatus
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.stats.merchant_metrics import check_size_bounds
from app.modules.stats.repository import StatsRepository
from app.modules.stats.service import StatsService
from app.modules.users.models import User


async def _mk_merchant(session, username: str) -> Merchant:
    u = User(
        username=username, password=get_password_hash("pass12345"),
        role=UserRole.MERCHANT, totp_enabled=False, is_blocked=False,
        use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    m = Merchant(
        user_id=u.id, api_key=f"k-{uuid4().hex[:8]}", api_secret=f"s-{uuid4().hex[:8]}",
        currency=Currency.RUB, fees={},
    )
    session.add(m)
    await session.flush()
    return m


async def _mk_order(
    session, *, merchant_id, amount, status,
    currency=Currency.RUB, direction=PaymentDirection.PAYIN,
) -> Order:
    o = Order(
        external_id=f"ext-{uuid4().hex[:8]}", merchant_id=merchant_id,
        direction=direction, payment_method=PaymentMethod.SBP,
        amount=Decimal(str(amount)), currency=currency, status=status,
        moderation_status=ModerationStatus.NONE,
    )
    session.add(o)
    await session.flush()
    return o


def _bucket_index(amount: float) -> int:
    for i, (lo, hi) in enumerate(check_size_bounds()):
        if amount >= lo and (hi is None or amount < hi):
            return i
    raise AssertionError(amount)


@pytest.mark.asyncio
async def test_all_merchants_aggregate_buckets_and_counts(session):
    m = await _mk_merchant(session, f"m_{uuid4().hex[:6]}")

    # SUCCESS RUB orders → counted + bucketed by amount (left-incl / right-excl).
    await _mk_order(session, merchant_id=m.id, amount=500, status=OrderStatus.SUCCESS)
    await _mk_order(session, merchant_id=m.id, amount=1000, status=OrderStatus.SUCCESS)   # → 1000–2000
    await _mk_order(session, merchant_id=m.id, amount=1999.99, status=OrderStatus.SUCCESS)  # → 1000–2000
    await _mk_order(session, merchant_id=m.id, amount=25000, status=OrderStatus.SUCCESS)  # → 20000+
    # created-but-not-success → counted in created, NOT in success/buckets.
    await _mk_order(session, merchant_id=m.id, amount=1500, status=OrderStatus.FAILED)
    # SUCCESS but USDT → counted in created/success, NOT in RUB buckets.
    await _mk_order(session, merchant_id=m.id, amount=3000, status=OrderStatus.SUCCESS, currency=Currency.USDT)
    # PAYOUT → excluded entirely from the payin funnel.
    await _mk_order(session, merchant_id=m.id, amount=800, status=OrderStatus.SUCCESS, direction=PaymentDirection.PAYOUT)

    rows = await StatsRepository(session).get_all_merchants_order_aggregates(None, None)
    row = next(r for r in rows if r.merchant_id == m.id)

    assert row.created == 6      # 4 RUB success + 1 failed + 1 USDT success (payout excluded)
    assert row.success == 5      # 4 RUB success + 1 USDT success

    buckets = [float(getattr(row, f"b{i}")) for i in range(len(check_size_bounds()))]
    assert buckets[_bucket_index(500)] == 500.0
    assert buckets[_bucket_index(1500)] == 1000.0 + 1999.99  # 1000 and 1999.99 both land in 1000–2000
    assert buckets[_bucket_index(25000)] == 25000.0
    # USDT 3000 and payout 800 must NOT appear in any RUB bucket.
    assert sum(buckets) == pytest.approx(500.0 + 1000.0 + 1999.99 + 25000.0)


@pytest.mark.asyncio
async def test_compute_merchant_stats_ratios_and_shape(session):
    m = await _mk_merchant(session, f"m_{uuid4().hex[:6]}")
    # 4 created, 3 success → conversion 75%. Requests come from CH (disabled → 0),
    # so payout_pct is 0 (created/requests, requests=0).
    for _ in range(3):
        await _mk_order(session, merchant_id=m.id, amount=1500, status=OrderStatus.SUCCESS)
    await _mk_order(session, merchant_id=m.id, amount=1500, status=OrderStatus.CANCELED)

    rows = await StatsService(session).get_merchant_stats(None, None)
    row = next(r for r in rows if r["merchant_id"] == m.id)

    assert row["orders_created"] == 4
    assert row["orders_success"] == 3
    assert row["conversion_pct"] == 75.0
    assert row["payout_pct"] == 0.0            # CH off → requests 0 → guarded
    assert row["requests"] == 0
    assert len(row["check_size_buckets"]) == 13
    assert row["merchant_login"] is not None   # resolved via Merchant→User join
