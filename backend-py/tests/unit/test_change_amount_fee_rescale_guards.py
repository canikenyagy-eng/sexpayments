"""
Unit (mock-based) adversarial guards for ``OrderService.change_amount``'s
trader-fee rescale (audit #19). The existing
``tests/unit/test_order_service.py::test_change_amount_rescales_trader_fee`` only
covers the happy ×2 case; this file isolates the GUARD branches that must NOT
rescale and must NOT crash — proven without a DB by asserting the exact ``fields``
dict handed to ``repository.update`` (the status write) carries / omits
``trader_fee_usdt`` as expected.

Mirrors the mock harness in test_order_service.py (AsyncContextManagerMock for the
nested tx, AsyncMock repository, stubbed FinanceService + celery).
"""
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.orders.service import OrderService
from app.modules.users.models import User


class AsyncContextManagerMock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        pass


@pytest.fixture
def mock_session():
    session = MagicMock()
    session.begin.return_value = AsyncContextManagerMock()
    session.begin_nested.return_value = AsyncContextManagerMock()
    session.flush = AsyncMock()
    session.execute = AsyncMock()
    session.get = AsyncMock()
    return session


@pytest.fixture
def service(mock_session):
    svc = OrderService(mock_session)
    svc.repository = AsyncMock()
    svc.audit_log = AsyncMock()
    svc._financial_snapshot = MagicMock(return_value={})
    return svc


@pytest.fixture
def mock_merchant():
    m = MagicMock(spec=Merchant)
    m.id = 1
    m.fees = {"card": 2.0, "sbp": 5.0}
    return m


def _order(**over):
    o = MagicMock(spec=Order)
    o.id = 100
    o.uuid = uuid.uuid4()
    o.direction = PaymentDirection.PAYIN
    o.amount = Decimal("1000")
    o.amount_usdt = Decimal("10")
    o.exchange_rate = Decimal("100")
    o.trader_fee_usdt = Decimal("0.20")
    o.merchant_id = 1
    o.trader_id = 5
    o.payment_method = PaymentMethod.CARD
    o.status = OrderStatus.DISPUTED
    o.financials = {}
    o.teamlead_reward_usdt = Decimal("0")
    for k, v in over.items():
        setattr(o, k, v)
    return o


async def _run_change_amount(service, mock_session, mock_merchant, order, new_amount):
    """Drive change_amount with finance + celery stubbed; return the FIRST
    repository.update fields dict (the status/figures write)."""
    service.repository.update = AsyncMock(return_value=order)
    trader = MagicMock(spec=User)
    trader.id = 5
    mock_session.get = AsyncMock(
        side_effect=lambda model, _id: mock_merchant if model is Merchant else trader
    )
    with patch("app.modules.finance.service.FinanceService") as fin_cls, \
         patch("app.modules.orders.service.celery_app"):
        fin = MagicMock()
        fin.recalculate_order = AsyncMock()
        fin_cls.return_value = fin
        out = await service.change_amount(order, new_amount, fire_callback=False)
    return out, service.repository.update


# ── guard: old_amount_usdt == 0 → no rescale, no ZeroDivision ───────────


@pytest.mark.asyncio
async def test_old_amount_usdt_zero_no_rescale_no_zerodivision(service, mock_session, mock_merchant):
    """A zero base amount must NOT divide-by-zero; the reward field is simply
    omitted from the update (no rescale)."""
    order = _order(amount=Decimal("0"), amount_usdt=Decimal("0"), trader_fee_usdt=Decimal("0.20"))

    # The call itself must not raise ZeroDivisionError.
    _out, update = await _run_change_amount(service, mock_session, mock_merchant, order, Decimal("500"))

    fields = update.call_args_list[0].args[1]
    assert "amount_usdt" in fields              # reprojected (rate present)
    assert "trader_fee_usdt" not in fields      # rescale guard short-circuited


# ── guard: exchange_rate falsy/None → whole reproject block skipped ─────


@pytest.mark.asyncio
@pytest.mark.parametrize("rate", [None, Decimal("0")])
async def test_no_exchange_rate_skips_reproject_and_rescale(service, mock_session, mock_merchant, rate):
    order = _order(exchange_rate=rate)

    _out, update = await _run_change_amount(service, mock_session, mock_merchant, order, Decimal("2000"))

    fields = update.call_args_list[0].args[1]
    # Without a rate, ONLY the fiat amount is written — no USDT figures, no reward.
    assert fields.get("amount") == Decimal("2000")
    assert "amount_usdt" not in fields
    assert "trader_fee_usdt" not in fields


# ── guard: trader_fee_usdt None/0 → reproject runs, reward not rescaled ──


@pytest.mark.asyncio
@pytest.mark.parametrize("fee", [None, Decimal("0")])
async def test_trader_fee_none_or_zero_no_rescale(service, mock_session, mock_merchant, fee):
    order = _order(trader_fee_usdt=fee)

    _out, update = await _run_change_amount(service, mock_session, mock_merchant, order, Decimal("2000"))

    fields = update.call_args_list[0].args[1]
    assert fields["amount_usdt"] == Decimal("20.0000")   # reprojected
    assert "trader_fee_usdt" not in fields               # nothing to rescale


# ── rounding: ratio needing quantize lands EXACTLY on 0.0000 ────────────


@pytest.mark.asyncio
async def test_rescale_quantizes_to_four_dp(service, mock_session, mock_merchant):
    """0.10 reward at amount_usdt 3 → change to amount_usdt 10 ⇒
    0.10 * (10/3) = 0.3333... quantized to 0.3333 exactly."""
    order = _order(amount=Decimal("300"), amount_usdt=Decimal("3"), trader_fee_usdt=Decimal("0.10"))

    _out, update = await _run_change_amount(service, mock_session, mock_merchant, order, Decimal("1000"))

    fields = update.call_args_list[0].args[1]
    assert fields["trader_fee_usdt"] == Decimal("0.3333")
    assert fields["trader_fee_usdt"] == fields["trader_fee_usdt"].quantize(Decimal("0.0000"))


# ── lowering the amount rescales DOWN (the existing unit only raises) ───


@pytest.mark.asyncio
async def test_lowering_amount_rescales_fee_down(service, mock_session, mock_merchant):
    order = _order()  # amount_usdt 10, fee 0.20

    _out, update = await _run_change_amount(service, mock_session, mock_merchant, order, Decimal("600"))

    fields = update.call_args_list[0].args[1]
    assert fields["amount_usdt"] == Decimal("6.0000")
    assert fields["trader_fee_usdt"] == Decimal("0.1200")   # 0.20 * (6/10)
