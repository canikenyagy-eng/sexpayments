"""
Integration tests for ``OrderService.complete_order_from_trader_group`` — the
trader-bot "✅ Подтвердить заявку (оплачено)" inline button.

It must run the SAME settlement as the cabinet ``complete_order`` while enforcing
GROUP-based authorisation: only the Telegram group bound to the order's assigned
trader may confirm it. These pin the auth branches + that the happy path actually
settles to SUCCESS against the real ledger.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.users import UserRole
from app.core.exceptions import ConflictException, ForbiddenException, NotFoundException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.orders.service import OrderService
from app.modules.traders.models import Trader
from app.modules.users.models import User

RATE = Decimal("10")
AMOUNT = Decimal("100.0000")
FEE = Decimal("5.0000")
TRADER_FEE = Decimal("2.0000")
GROUP_ID = -1001234567890   # a Telegram supergroup id


@pytest.fixture(autouse=True)
def _stub_celery():
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.modules.orders.service.celery_app", celery), \
         patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


async def _mk_user(session, *, role=UserRole.TRADER) -> User:
    u = User(username=f"{role.value}_{uuid4().hex[:6]}", password=get_password_hash("pass12345"),
             role=role, totp_enabled=False, is_blocked=False, use_shared_balance=True)
    session.add(u)
    await session.flush()
    return u


async def _mk_trader_profile(session, *, user_id, telegram_group_id=None) -> Trader:
    t = Trader(user_id=user_id, telegram_group_id=telegram_group_id)
    session.add(t)
    await session.flush()
    return t


async def _mk_merchant(session, *, user_id) -> Merchant:
    s = uuid4().hex[:6]
    m = Merchant(user_id=user_id, name=f"M-{s}", status=TerminalStatus.ENABLED,
                 currency=Currency.RUB, api_key=f"key-{s}", api_secret=f"secret-{s}",
                 fees={PaymentMethod.SBP.value: 5})
    session.add(m)
    await session.flush()
    return m


async def _mk_balance(session, amount, *, user_id=None, merchant_id=None, btype=BalanceType.WORK):
    b = Balance(user_id=user_id, merchant_id=merchant_id, is_system=False,
                type=btype, currency=Currency.USDT, amount=Decimal(amount))
    session.add(b)
    await session.flush()
    return b


async def _mk_order(session, *, merchant_id, trader_id, status=OrderStatus.PENDING) -> Order:
    o = Order(uuid=uuid4(), external_id=f"ext-{uuid4().hex[:8]}", merchant_id=merchant_id,
              trader_id=trader_id, direction=PaymentDirection.PAYIN, payment_method=PaymentMethod.SBP,
              amount=Decimal("1000.00"), currency=Currency.RUB, exchange_rate=RATE,
              amount_usdt=AMOUNT, fee_usdt=FEE, trader_fee_usdt=TRADER_FEE,
              profit_usdt=AMOUNT - FEE, status=status)
    session.add(o)
    await session.flush()
    return o


async def _bal(session, *, user_id=None, merchant_id=None, btype=BalanceType.WORK) -> Decimal:
    from sqlalchemy import select
    stmt = select(Balance).where(Balance.type == btype, Balance.currency == Currency.USDT)
    if user_id is not None:
        stmt = stmt.where(Balance.user_id == user_id)
    elif merchant_id is not None:
        stmt = stmt.where(Balance.merchant_id == merchant_id)
    row = (await session.execute(stmt)).scalars().first()
    return row.amount if row else Decimal("0")


async def _scaffold(session, *, group_id=GROUP_ID, status=OrderStatus.PENDING):
    trader = await _mk_user(session)
    await _mk_trader_profile(session, user_id=trader.id, telegram_group_id=group_id)
    mu = await _mk_user(session, role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id)
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.ESCROW)  # frozen collateral
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=status)
    return trader, merchant, order


# ── happy path: same settlement as the cabinet button ───────────────────


@pytest.mark.asyncio
async def test_group_confirm_settles_order_to_success(session):
    trader, merchant, order = await _scaffold(session)

    out = await OrderService(session).complete_order_from_trader_group(
        order_uuid=str(order.uuid), telegram_group_id=GROUP_ID, actor_tg_id=999,
    )

    assert out.status == OrderStatus.SUCCESS
    # Escrow settled to the merchant; trader keeps only the processing fee.
    assert await _bal(session, merchant_id=merchant.id) == AMOUNT - FEE   # 95
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, user_id=trader.id) == TRADER_FEE


# ── group-based authorisation ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_wrong_group_is_forbidden(session):
    trader, merchant, order = await _scaffold(session)
    with pytest.raises(ForbiddenException):
        await OrderService(session).complete_order_from_trader_group(
            order_uuid=str(order.uuid), telegram_group_id=-100999,  # not this trader's group
        )
    assert (await session.get(Order, order.id)).status == OrderStatus.PENDING


@pytest.mark.asyncio
async def test_trader_without_group_is_forbidden(session):
    trader, merchant, order = await _scaffold(session, group_id=None)
    with pytest.raises(ForbiddenException):
        await OrderService(session).complete_order_from_trader_group(
            order_uuid=str(order.uuid), telegram_group_id=GROUP_ID,
        )


# ── not-found / conflict branches ───────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_order_is_not_found(session):
    with pytest.raises(NotFoundException):
        await OrderService(session).complete_order_from_trader_group(
            order_uuid=str(uuid4()), telegram_group_id=GROUP_ID,
        )


@pytest.mark.asyncio
async def test_second_confirm_is_idempotent(session):
    """Re-confirming an already-SUCCESS order is a no-op, not a conflict: the
    second confirm returns the settled order and moves NO money again (the
    double-completion guard). Previously this raised ConflictException; the
    idempotency fix makes a repeat confirm return the order unchanged."""
    trader, merchant, order = await _scaffold(session)
    svc = OrderService(session)
    out1 = await svc.complete_order_from_trader_group(order_uuid=str(order.uuid), telegram_group_id=GROUP_ID)
    assert out1.status == OrderStatus.SUCCESS
    merchant_after_first = await _bal(session, merchant_id=merchant.id)
    trader_after_first = await _bal(session, user_id=trader.id)

    # Second confirm → idempotent return, NO ConflictException, NO second settlement.
    out2 = await svc.complete_order_from_trader_group(order_uuid=str(order.uuid), telegram_group_id=GROUP_ID)
    assert out2.status == OrderStatus.SUCCESS
    assert await _bal(session, merchant_id=merchant.id) == merchant_after_first
    assert await _bal(session, user_id=trader.id) == trader_after_first


@pytest.mark.asyncio
async def test_order_without_trader_conflicts(session):
    mu = await _mk_user(session, role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id)
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=None)
    with pytest.raises(ConflictException):
        await OrderService(session).complete_order_from_trader_group(
            order_uuid=str(order.uuid), telegram_group_id=GROUP_ID,
        )
