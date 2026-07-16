"""
Phase-3 payout terminal logic (real in-memory DB): terminal CRUD + balance
top-up, the trader ACL on the pool, claim-ACL enforcement, and create_payout
converting at the terminal's rate / charging its commission / freezing the
terminal balance.
"""
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.common.enums.rates import OrderBookSide, RateSource
from app.common.enums.users import UserRole
from app.core.exceptions import ForbiddenException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance
from app.modules.payouts.service import PayoutService
from app.modules.payouts.terminal_service import PayoutTerminalService
from app.modules.rates.models import RateConfig
from app.modules.traders.models import Trader
from app.modules.users.models import User
from sqlalchemy import select


@pytest.fixture(autouse=True)
def _stub_celery():
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


async def _mk_user(session, *, role=UserRole.TRADER) -> User:
    u = User(username=f"{role.value}_{uuid4().hex[:6]}", password=get_password_hash("pass12345"),
             role=role, totp_enabled=False, is_blocked=False, use_shared_balance=True)
    session.add(u)
    await session.flush()
    return u


async def _mk_trader_profile(session, *, user_id) -> Trader:
    t = Trader(user_id=user_id, is_payout_active=True, payout_fee_percent=Decimal("2.00"), payout_hold_hours=0)
    session.add(t)
    await session.flush()
    return t


async def _mk_rate(session) -> RateConfig:
    rc = RateConfig(name="RUB top1", source=RateSource.BYBIT, side=OrderBookSide.BUY,
                    position=1, fiat_currency=Currency.RUB, crypto_currency="USDT",
                    is_active=True, current_rate=10.0)
    session.add(rc)
    await session.flush()
    return rc


def _create_data(*, owner_id, trader_ids, rate_config_id):
    return SimpleNamespace(
        owner_user_id=owner_id, name="Terminal A", status=None, currency=Currency.RUB,
        rate_config_id=rate_config_id, commission_percent=10, ttl_minutes=30,
        receipts_to_close=1, min_amount=None, max_amount=None, webhook_url=None,
        trader_ids=trader_ids,
    )


def _payout_data(external_id):
    return SimpleNamespace(
        amount=1000, currency=Currency.RUB, payment_method=PaymentMethod.SBP, payment_option=None,
        external_id=external_id, user_id="enduser-1", notification_url=None,
        payment_requisites=SimpleNamespace(holder="End User", number="40817810099910000001", extra=None),
    )


async def _bal(session, *, payout_terminal_id, btype=BalanceType.WORK) -> Decimal:
    row = (await session.execute(
        select(Balance).where(Balance.payout_terminal_id == payout_terminal_id,
                              Balance.type == btype, Balance.currency == Currency.USDT)
    )).scalars().first()
    return row.amount if row else Decimal("0")


@pytest.mark.asyncio
async def test_create_terminal_seeds_keys_balances_and_acl(session):
    owner = await _mk_user(session, role=UserRole.MERCHANT)
    trader = await _mk_user(session)
    svc = PayoutTerminalService(session)

    terminal, api_key, api_secret = await svc.create_terminal(
        _create_data(owner_id=owner.id, trader_ids=[trader.id], rate_config_id=None)
    )

    assert api_key and api_secret and terminal.api_key == api_key
    assert terminal.api_secret != api_secret           # stored encrypted
    assert await svc.get_by_api_key(api_key) is not None
    assert await svc.trader_ids(terminal.id) == [trader.id]
    # WORK + ESCROW balances seeded at 0.
    assert await _bal(session, payout_terminal_id=terminal.id) == Decimal("0")
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")


@pytest.mark.asyncio
async def test_topup_credits_work_balance(session):
    owner = await _mk_user(session, role=UserRole.MERCHANT)
    svc = PayoutTerminalService(session)
    terminal, *_ = await svc.create_terminal(_create_data(owner_id=owner.id, trader_ids=[], rate_config_id=None))

    new_bal = await svc.topup(terminal.id, 500)
    assert new_bal == Decimal("500")
    assert await _bal(session, payout_terminal_id=terminal.id) == Decimal("500")


@pytest.mark.asyncio
async def test_create_payout_converts_charges_and_freezes(session):
    owner = await _mk_user(session, role=UserRole.MERCHANT)
    rate = await _mk_rate(session)
    tsvc = PayoutTerminalService(session)
    terminal, *_ = await tsvc.create_terminal(
        _create_data(owner_id=owner.id, trader_ids=[], rate_config_id=rate.id)
    )
    await tsvc.topup(terminal.id, 1000)   # USDT, covers the freeze

    payout = await PayoutService(session).create_payout(terminal.id, _payout_data("p-1"))

    # 1000 RUB @ rate 10 → 100 USDT; commission 10% → fee 10; freeze 110.
    assert payout.amount_usdt == Decimal("100.0000")
    assert payout.merchant_fee_usdt == Decimal("10.0000")
    assert payout.status == PayoutStatus.CREATED
    assert await _bal(session, payout_terminal_id=terminal.id) == Decimal("890")             # 1000 − 110
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("110")


@pytest.mark.asyncio
async def test_acl_pool_and_claim_enforced(session):
    owner = await _mk_user(session, role=UserRole.MERCHANT)
    rate = await _mk_rate(session)
    bound = await _mk_user(session)
    await _mk_trader_profile(session, user_id=bound.id)
    outsider = await _mk_user(session)
    await _mk_trader_profile(session, user_id=outsider.id)

    tsvc = PayoutTerminalService(session)
    terminal, *_ = await tsvc.create_terminal(
        _create_data(owner_id=owner.id, trader_ids=[bound.id], rate_config_id=rate.id)
    )
    await tsvc.topup(terminal.id, 1000)
    psvc = PayoutService(session)
    payout = await psvc.create_payout(terminal.id, _payout_data("p-acl"))

    # Bound trader sees it; outsider sees an empty pool.
    assert [p.id for p in await psvc.list_pool_for_trader(bound)] == [payout.id]
    assert await psvc.list_pool_for_trader(outsider) == []

    # Outsider cannot claim; bound trader can.
    with pytest.raises(ForbiddenException):
        await psvc.claim_payout(str(payout.uuid), outsider)
    claimed = await psvc.claim_payout(str(payout.uuid), bound)
    assert claimed.status == PayoutStatus.CLAIMED and claimed.trader_id == bound.id
