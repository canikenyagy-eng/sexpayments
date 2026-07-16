"""
Teamlead rewards on PAYOUT settlement (real in-memory ledger).

Payouts run on payout TERMINALS (no merchant entity), so only the TRADER-side
teamlead links apply. On payout COMPLETE, ``settle_payout`` folds in
``TeamleaderService.calculate_and_pay_payout_rewards``: every active teamlead
link of the EXECUTING trader with a positive ``payout_fee_percent`` is paid
``amount_usdt × %`` from the system balance → teamlead WORK. Reference ids are
namespaced ``payout:{id}`` so payout rewards never pollute order-id reward stats.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.payouts.models import Payout, PayoutTerminal
from app.modules.payouts.service import PayoutService
from app.modules.teamleaders.models import TeamleadLink
from app.modules.teamleaders.service import TeamleaderService
from app.modules.traders.models import Trader
from app.modules.users.models import User

AMOUNT = Decimal("100.0000")
MERCHANT_FEE = Decimal("5.0000")
TRADER_FEE = Decimal("2.0000")
FREEZE = AMOUNT + MERCHANT_FEE
SYSTEM_SEED = Decimal("1000")   # pre-funded system WORK to cover rewards


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
    t = Trader(user_id=user_id, is_payout_active=True, payout_fee_percent=Decimal("2.00"),
               payout_hold_hours=0)
    session.add(t)
    await session.flush()
    return t


async def _mk_terminal(session, *, owner_id) -> PayoutTerminal:
    s = uuid4().hex[:6]
    t = PayoutTerminal(user_id=owner_id, name=f"PT-{s}", status=TerminalStatus.ENABLED,
                       currency=Currency.RUB, api_key=f"pk-{s}", api_secret=f"ps-{s}",
                       commission_percent=Decimal("5.00"), ttl_minutes=60, receipts_to_close=1)
    session.add(t)
    await session.flush()
    return t


async def _mk_balance(session, amount, *, user_id=None, payout_terminal_id=None, is_system=False, btype=BalanceType.WORK):
    b = Balance(user_id=user_id, payout_terminal_id=payout_terminal_id, is_system=is_system,
                type=btype, currency=Currency.USDT, amount=Decimal(amount))
    session.add(b)
    await session.flush()
    return b


async def _mk_link(session, *, teamlead_id, entity_type, entity_id, fee_percent="0", payout_fee_percent="0"):
    link = TeamleadLink(teamlead_id=teamlead_id, linked_entity_type=entity_type,
                        linked_entity_id=entity_id, fee_percent=Decimal(fee_percent),
                        payout_fee_percent=Decimal(payout_fee_percent), is_active=True)
    session.add(link)
    await session.flush()
    return link


async def _mk_payout(session, *, terminal_id, trader_id, status=PayoutStatus.CLAIMED) -> Payout:
    p = Payout(uuid=uuid4(), external_id=f"ext-{uuid4().hex[:8]}", payout_terminal_id=terminal_id,
               trader_id=trader_id, payment_method=PaymentMethod.SBP, amount=Decimal("1000.00"),
               currency=Currency.RUB, exchange_rate=Decimal("10"), amount_usdt=AMOUNT,
               merchant_fee_usdt=MERCHANT_FEE, trader_fee_usdt=TRADER_FEE,
               req_holder="End User", req_number="40817810099910000001", status=status)
    session.add(p)
    await session.flush()
    return p


async def _bal(session, *, user_id=None, payout_terminal_id=None, is_system=False, btype=BalanceType.WORK) -> Decimal:
    stmt = select(Balance).where(Balance.type == btype, Balance.currency == Currency.USDT)
    if is_system:
        stmt = stmt.where(Balance.is_system.is_(True))
    elif user_id is not None:
        stmt = stmt.where(Balance.user_id == user_id)
    elif payout_terminal_id is not None:
        stmt = stmt.where(Balance.payout_terminal_id == payout_terminal_id)
    row = (await session.execute(stmt)).scalars().first()
    return row.amount if row else Decimal("0")


async def _scaffold(session, *, terminal_escrow=FREEZE, system_seed=SYSTEM_SEED):
    owner = await _mk_user(session, role=UserRole.MERCHANT)
    terminal = await _mk_terminal(session, owner_id=owner.id)
    await _mk_balance(session, terminal_escrow, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW)
    if system_seed:
        await _mk_balance(session, system_seed, is_system=True)
    trader = await _mk_user(session, role=UserRole.TRADER)
    await _mk_trader_profile(session, user_id=trader.id)
    payout = await _mk_payout(session, terminal_id=terminal.id, trader_id=trader.id)
    return terminal, trader, payout


# ── trader-side teamlead paid + value conservation ──────────────────────


@pytest.mark.asyncio
async def test_payout_pays_trader_teamlead(session):
    terminal, trader, payout = await _scaffold(session)
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    await _mk_link(session, teamlead_id=tl.id, entity_type=UserRole.TRADER,
                   entity_id=trader.id, payout_fee_percent="3")   # 3% of 100 = 3

    await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)

    reward = Decimal("3")
    assert await _bal(session, user_id=tl.id) == reward
    assert await _bal(session, user_id=trader.id) == AMOUNT + TRADER_FEE          # 102
    # System: seed + commission − trader_fee − teamlead.
    assert await _bal(session, is_system=True) == SYSTEM_SEED + MERCHANT_FEE - TRADER_FEE - reward

    total_after = (
        await _bal(session, user_id=trader.id)
        + await _bal(session, is_system=True)
        + await _bal(session, user_id=tl.id)
        + await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW)
    )
    assert total_after == FREEZE + SYSTEM_SEED


# ── merchant-side link is IGNORED on payouts ────────────────────────────


@pytest.mark.asyncio
async def test_merchant_side_link_not_paid_on_payout(session):
    """A teamlead linked to a MERCHANT earns nothing on payouts (no merchant)."""
    terminal, trader, payout = await _scaffold(session)
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    # Link to some merchant id — irrelevant to payouts.
    await _mk_link(session, teamlead_id=tl.id, entity_type=UserRole.MERCHANT,
                   entity_id=999, payout_fee_percent="5")

    await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)
    assert await _bal(session, user_id=tl.id) == Decimal("0")


# ── payin % is independent of payout % ──────────────────────────────────


@pytest.mark.asyncio
async def test_payout_uses_payout_percent_not_payin_percent(session):
    terminal, trader, payout = await _scaffold(session)
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    await _mk_link(session, teamlead_id=tl.id, entity_type=UserRole.TRADER,
                   entity_id=trader.id, fee_percent="10", payout_fee_percent="2")

    await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)
    assert await _bal(session, user_id=tl.id) == Decimal("2")   # 2% of 100, NOT 10%


@pytest.mark.asyncio
async def test_zero_payout_percent_pays_no_reward(session):
    terminal, trader, payout = await _scaffold(session)
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    await _mk_link(session, teamlead_id=tl.id, entity_type=UserRole.TRADER,
                   entity_id=trader.id, fee_percent="10", payout_fee_percent="0")

    await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)
    assert await _bal(session, user_id=tl.id) == Decimal("0")


# ── several teamleads on one trader ─────────────────────────────────────


@pytest.mark.asyncio
async def test_multiple_teamleads_on_one_trader(session):
    terminal, trader, payout = await _scaffold(session)
    tl1 = await _mk_user(session, role=UserRole.TEAMLEAD)
    tl2 = await _mk_user(session, role=UserRole.TEAMLEAD)
    await _mk_link(session, teamlead_id=tl1.id, entity_type=UserRole.TRADER,
                   entity_id=trader.id, payout_fee_percent="1.5")
    await _mk_link(session, teamlead_id=tl2.id, entity_type=UserRole.TRADER,
                   entity_id=trader.id, payout_fee_percent="0.5")

    await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)
    assert await _bal(session, user_id=tl1.id) == Decimal("1.5")
    assert await _bal(session, user_id=tl2.id) == Decimal("0.5")


# ── no reward on a refund (cancel) ──────────────────────────────────────


@pytest.mark.asyncio
async def test_no_teamlead_reward_on_cancel(session):
    terminal, trader, payout = await _scaffold(session)
    payout.status = PayoutStatus.CREATED   # cancelable
    await session.flush()
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    await _mk_link(session, teamlead_id=tl.id, entity_type=UserRole.TRADER,
                   entity_id=trader.id, payout_fee_percent="5")

    await PayoutService(session).change_status(payout, PayoutStatus.CANCELED)
    assert await _bal(session, user_id=tl.id) == Decimal("0")
    assert await _bal(session, payout_terminal_id=terminal.id) == FREEZE
    assert await _bal(session, is_system=True) == SYSTEM_SEED


# ── namespaced reference keeps order stats clean ────────────────────────


@pytest.mark.asyncio
async def test_payout_reward_reference_is_namespaced_and_not_counted_as_order(session):
    terminal, trader, payout = await _scaffold(session)
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    await _mk_link(session, teamlead_id=tl.id, entity_type=UserRole.TRADER,
                   entity_id=trader.id, payout_fee_percent="4")

    await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)

    entry = (await session.execute(
        select(LedgerEntry).where(
            LedgerEntry.reference_type == LedgerReferenceType.TEAMLEAD_REWARD,
            LedgerEntry.reference_id == f"payout:{payout.id}",
        )
    )).scalars().first()
    assert entry is not None and entry.amount == Decimal("4")

    stats = await TeamleaderService(session).get_teamlead_stats(tl.id)
    assert stats["total_earned_usdt"] == Decimal("4")
    assert stats["orders_count"] == 0
