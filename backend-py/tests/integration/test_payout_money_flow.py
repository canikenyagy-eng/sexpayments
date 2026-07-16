"""
Integration tests for the PAYOUT money flow against a REAL in-memory ledger.

Payouts run on a dedicated **PayoutTerminal** (own balance, rate, commission).
Model (terminal-funded; mirror of payin in reverse):
  * create  → freeze terminal WORK → ESCROW for amount_usdt + commission.
  * claim   → no money (trader takes it; sets trader fee).
  * COMPLETE→ settle: terminal ESCROW → trader (amount) + terminal ESCROW → system
              (commission) + system → trader (fee). Trader earnings to WORK, or to
              ESCROW (held) when the trader has payout_hold_hours > 0.
  * CANCELED/EXPIRED → refund: terminal ESCROW → WORK (amount + commission).
  * hold release → trader ESCROW → WORK (amount + trader fee).

All transitions go through PayoutService.change_status; the money lives in
FinanceService.{freeze,settle,refund,release}_payout on the TERMINAL balance.
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.finance.models import Balance
from app.modules.payouts.exceptions import PayoutConflict
from app.modules.payouts.models import Payout, PayoutTerminal
from app.modules.payouts.service import PayoutService
from app.modules.traders.models import Trader
from app.modules.users.models import User

AMOUNT = Decimal("100.0000")        # amount_usdt sent to the end-user
MERCHANT_FEE = Decimal("5.0000")    # commission charged to the merchant
TRADER_FEE = Decimal("2.0000")      # trader reward
FREEZE = AMOUNT + MERCHANT_FEE      # 105 — frozen at creation


@pytest.fixture(autouse=True)
def _stub_celery():
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


# ── builders ──────────────────────────────────────────────────────────


async def _mk_user(session, *, username, role=UserRole.TRADER) -> User:
    u = User(
        username=username, password=get_password_hash("pass12345"), role=role,
        totp_enabled=False, is_blocked=False, use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_trader_profile(session, *, user_id, hold_hours=0) -> Trader:
    t = Trader(user_id=user_id, is_payout_active=True, payout_fee_percent=Decimal("2.00"),
               payout_hold_hours=hold_hours)
    session.add(t)
    await session.flush()
    return t


async def _mk_terminal(session, *, owner_id, suffix) -> PayoutTerminal:
    t = PayoutTerminal(
        user_id=owner_id, name=f"PT-{suffix}", status=TerminalStatus.ENABLED,
        currency=Currency.RUB, api_key=f"pk-{suffix}", api_secret=f"ps-{suffix}",
        commission_percent=Decimal("5.00"), ttl_minutes=60, receipts_to_close=1,
    )
    session.add(t)
    await session.flush()
    return t


async def _mk_balance(session, amount, *, user_id=None, payout_terminal_id=None, is_system=False, btype=BalanceType.WORK) -> Balance:
    b = Balance(user_id=user_id, payout_terminal_id=payout_terminal_id, is_system=is_system,
                type=btype, currency=Currency.USDT, amount=Decimal(amount))
    session.add(b)
    await session.flush()
    return b


async def _mk_payout(session, *, terminal_id, trader_id=None, status=PayoutStatus.CREATED) -> Payout:
    p = Payout(
        uuid=uuid4(), external_id=f"ext-{uuid4().hex[:8]}", payout_terminal_id=terminal_id,
        trader_id=trader_id, payment_method=PaymentMethod.SBP,
        amount=Decimal("1000.00"), currency=Currency.RUB, exchange_rate=Decimal("10"),
        amount_usdt=AMOUNT, merchant_fee_usdt=MERCHANT_FEE,
        trader_fee_usdt=(TRADER_FEE if trader_id else None),
        req_holder="End User", req_number="40817810099910000001", status=status,
    )
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


async def _setup(session, *, payout_status=PayoutStatus.CREATED, with_trader=True, hold_hours=0,
                 terminal_work=Decimal("0"), terminal_escrow=Decimal("0")):
    owner = await _mk_user(session, username=f"ow_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    terminal = await _mk_terminal(session, owner_id=owner.id, suffix=uuid4().hex[:6])
    if terminal_work:
        await _mk_balance(session, terminal_work, payout_terminal_id=terminal.id)
    if terminal_escrow:
        await _mk_balance(session, terminal_escrow, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW)
    trader = None
    if with_trader:
        trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
        await _mk_trader_profile(session, user_id=trader.id, hold_hours=hold_hours)
    payout = await _mk_payout(session, terminal_id=terminal.id,
                              trader_id=(trader.id if trader else None), status=payout_status)
    return terminal, trader, payout


# ── create → freeze ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_freeze_payout_moves_terminal_work_to_escrow(session):
    terminal, _t, payout = await _setup(session, with_trader=False, terminal_work=Decimal("200"))
    await PayoutService(session).finance.freeze_payout(payout, terminal)

    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == FREEZE
    assert await _bal(session, payout_terminal_id=terminal.id) == Decimal("200") - FREEZE


# ── COMPLETE → settle ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_complete_payout_settles_to_trader_and_platform(session):
    """terminal ESCROW(105) → trader 100, system commission 5, system pays trader
    fee 2 → trader WORK 102, system 3 (platform profit), terminal ESCROW 0."""
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=FREEZE,
    )
    out = await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)

    assert out.status == PayoutStatus.COMPLETED
    assert await _bal(session, user_id=trader.id) == AMOUNT + TRADER_FEE          # 102
    assert await _bal(session, is_system=True) == MERCHANT_FEE - TRADER_FEE       # 3
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")
    # Value conserved: 105 frozen → 100+2 (trader) + 3 (system) = 105.
    assert (AMOUNT + TRADER_FEE) + (MERCHANT_FEE - TRADER_FEE) == FREEZE


@pytest.mark.asyncio
async def test_complete_payout_with_hold_parks_trader_earnings_in_escrow(session):
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, hold_hours=24, terminal_escrow=FREEZE,
    )
    out = await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)

    # Trader earnings (amount + fee) held in ESCROW, not WORK.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT + TRADER_FEE
    assert await _bal(session, user_id=trader.id) == Decimal("0")
    assert out.trader_hold_until is not None

    # Hold release → ESCROW → WORK.
    await PayoutService(session).release_hold(out)
    assert await _bal(session, user_id=trader.id) == AMOUNT + TRADER_FEE
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")


# ── CANCELED / EXPIRED → refund ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_payout_refunds_terminal(session):
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CREATED, terminal_escrow=FREEZE,
    )
    out = await PayoutService(session).change_status(payout, PayoutStatus.CANCELED)

    assert out.status == PayoutStatus.CANCELED
    assert await _bal(session, payout_terminal_id=terminal.id) == FREEZE              # full refund
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")


@pytest.mark.asyncio
async def test_expire_payout_refunds_terminal(session):
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=FREEZE,
    )
    await PayoutService(session).expire_payout(payout)

    refreshed = await session.get(Payout, payout.id)
    assert refreshed.status == PayoutStatus.EXPIRED
    assert await _bal(session, payout_terminal_id=terminal.id) == FREEZE


# ── claim (no money) + guards ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_moves_no_money(session):
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CREATED, terminal_escrow=FREEZE,
    )
    await PayoutService(session).change_status(
        payout, PayoutStatus.CLAIMED, extra_fields={"trader_id": trader.id},
    )
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == FREEZE
    assert await _bal(session, user_id=trader.id) == Decimal("0")


@pytest.mark.asyncio
async def test_illegal_payout_transition_rejected(session):
    """A COMPLETED payout can't be moved anywhere (terminal)."""
    terminal, trader, payout = await _setup(session, payout_status=PayoutStatus.COMPLETED)
    with pytest.raises(PayoutConflict, match="Illegal payout transition"):
        await PayoutService(session).change_status(payout, PayoutStatus.CANCELED)


@pytest.mark.asyncio
async def test_return_stale_claim_clears_trader_fields_no_money(session):
    terminal, trader, payout = await _setup(
        session, payout_status=PayoutStatus.CLAIMED, terminal_escrow=FREEZE,
    )
    await PayoutService(session).return_stale_claim(payout)

    refreshed = await session.get(Payout, payout.id)
    assert refreshed.status == PayoutStatus.CREATED
    assert refreshed.trader_id is None
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == FREEZE
