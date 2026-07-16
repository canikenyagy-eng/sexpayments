"""
Multi-receipt payout close (real in-memory ledger).

A payout is closed by up to ``terminal.receipts_to_close`` PARTIAL receipts, each
its own fiat amount. The APPROVED amounts must sum to EXACTLY the payout amount
(no over/under); each receipt is moderated individually (reject → re-upload only
that one); money SETTLES ONCE, on the full amount, at completion.
"""
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutReceiptStatus, PayoutStatus
from app.common.enums.users import UserRole
from app.core.exceptions import ValidationException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance
from app.modules.payouts.exceptions import PayoutConflict
from app.modules.payouts.models import Payout, PayoutReceipt, PayoutTerminal
from app.modules.payouts.service import PayoutService
from app.modules.traders.models import Trader
from app.modules.users.models import User

AMOUNT_FIAT = Decimal("1000.00")    # full payout amount (fiat)
AMOUNT_USDT = Decimal("100.0000")
MERCHANT_FEE = Decimal("5.0000")
TRADER_FEE = Decimal("2.0000")
FREEZE = AMOUNT_USDT + MERCHANT_FEE
SYSTEM_SEED = Decimal("1000")


@pytest.fixture(autouse=True)
def _stub():
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    # Skip real file IO — receipt accounting/money is what we test.
    with patch("app.workers.celery_app.celery_app", celery, create=True), \
         patch.object(PayoutService, "_save_receipt", new=AsyncMock(return_value="/tmp/r.jpg")):
        yield


async def _mk_user(session, *, role=UserRole.TRADER) -> User:
    u = User(username=f"{role.value}_{uuid4().hex[:6]}", password=get_password_hash("pass12345"),
             role=role, totp_enabled=False, is_blocked=False, use_shared_balance=True)
    session.add(u)
    await session.flush()
    return u


async def _mk_trader(session, *, user_id, receipt_auto=True) -> Trader:
    t = Trader(user_id=user_id, is_payout_active=True, payout_fee_percent=Decimal("2.00"),
               payout_hold_hours=0, payout_receipt_auto=receipt_auto)
    session.add(t)
    await session.flush()
    return t


async def _mk_terminal(session, *, owner_id, receipts_to_close=1) -> PayoutTerminal:
    s = uuid4().hex[:6]
    t = PayoutTerminal(user_id=owner_id, name=f"PT-{s}", status=TerminalStatus.ENABLED,
                       currency=Currency.RUB, api_key=f"pk-{s}", api_secret=f"ps-{s}",
                       commission_percent=Decimal("5.00"), ttl_minutes=60,
                       receipts_to_close=receipts_to_close)
    session.add(t)
    await session.flush()
    return t


async def _mk_balance(session, amount, *, user_id=None, payout_terminal_id=None, is_system=False, btype=BalanceType.WORK):
    b = Balance(user_id=user_id, payout_terminal_id=payout_terminal_id, is_system=is_system,
                type=btype, currency=Currency.USDT, amount=Decimal(amount))
    session.add(b)
    await session.flush()
    return b


async def _mk_payout(session, *, terminal_id, trader_id) -> Payout:
    p = Payout(uuid=uuid4(), external_id=f"ext-{uuid4().hex[:8]}", payout_terminal_id=terminal_id,
               trader_id=trader_id, payment_method=PaymentMethod.SBP, amount=AMOUNT_FIAT,
               currency=Currency.RUB, exchange_rate=Decimal("10"), amount_usdt=AMOUNT_USDT,
               merchant_fee_usdt=MERCHANT_FEE, trader_fee_usdt=TRADER_FEE,
               req_holder="End User", req_number="40817810099910000001", status=PayoutStatus.CLAIMED)
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


async def _scaffold(session, *, receipts_to_close=1, receipt_auto=True):
    owner = await _mk_user(session, role=UserRole.MERCHANT)
    terminal = await _mk_terminal(session, owner_id=owner.id, receipts_to_close=receipts_to_close)
    await _mk_balance(session, FREEZE, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW)
    await _mk_balance(session, SYSTEM_SEED, is_system=True)
    trader = await _mk_user(session)
    await _mk_trader(session, user_id=trader.id, receipt_auto=receipt_auto)
    payout = await _mk_payout(session, terminal_id=terminal.id, trader_id=trader.id)
    return terminal, trader, payout


async def _receipts(session, payout_id):
    return (await session.execute(
        select(PayoutReceipt).where(PayoutReceipt.payout_id == payout_id).order_by(PayoutReceipt.id)
    )).scalars().all()


def _assert_settled(amount_to_trader=AMOUNT_USDT + TRADER_FEE):
    pass


# ── single full receipt (N=1, auto) — back-compat ───────────────────────


@pytest.mark.asyncio
async def test_single_full_receipt_auto_completes_and_settles(session):
    terminal, trader, payout = await _scaffold(session, receipts_to_close=1, receipt_auto=True)
    svc = PayoutService(session)

    out = await svc.add_receipt(str(payout.uuid), trader, None, AMOUNT_FIAT)

    assert out.status == PayoutStatus.COMPLETED
    assert await _bal(session, user_id=trader.id) == AMOUNT_USDT + TRADER_FEE       # 102, settled once
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")


# ── partial receipts close on full sum (N=3, auto) ──────────────────────


@pytest.mark.asyncio
async def test_partial_receipts_complete_on_full_sum_settle_once(session):
    terminal, trader, payout = await _scaffold(session, receipts_to_close=3, receipt_auto=True)
    svc = PayoutService(session)

    out1 = await svc.add_receipt(str(payout.uuid), trader, None, Decimal("400"))
    assert out1.status == PayoutStatus.CLAIMED                       # not yet covered
    assert await _bal(session, user_id=trader.id) == Decimal("0")    # nothing settled yet
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == FREEZE

    out2 = await svc.add_receipt(str(payout.uuid), trader, None, Decimal("600"))   # 400+600 = 1000
    assert out2.status == PayoutStatus.COMPLETED
    # Settled ONCE on the full amount_usdt, regardless of the 2-receipt split.
    assert await _bal(session, user_id=trader.id) == AMOUNT_USDT + TRADER_FEE       # 102
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, is_system=True) == SYSTEM_SEED + MERCHANT_FEE - TRADER_FEE


# ── over / under / count guards ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_overpay_rejected(session):
    terminal, trader, payout = await _scaffold(session, receipts_to_close=3, receipt_auto=True)
    svc = PayoutService(session)
    await svc.add_receipt(str(payout.uuid), trader, None, Decimal("600"))   # remaining 400
    with pytest.raises(ValidationException, match="exceeds remaining"):
        await svc.add_receipt(str(payout.uuid), trader, None, Decimal("600"))


@pytest.mark.asyncio
async def test_receipt_count_capped_blocks_close(session):
    terminal, trader, payout = await _scaffold(session, receipts_to_close=2, receipt_auto=True)
    svc = PayoutService(session)
    await svc.add_receipt(str(payout.uuid), trader, None, Decimal("300"))
    await svc.add_receipt(str(payout.uuid), trader, None, Decimal("300"))   # 2 used, sum 600 < 1000
    with pytest.raises(PayoutConflict, match="Max receipts"):
        await svc.add_receipt(str(payout.uuid), trader, None, Decimal("400"))
    # Still open (under-covered), nothing settled.
    assert (await session.get(Payout, payout.id)).status == PayoutStatus.CLAIMED
    assert await _bal(session, user_id=trader.id) == Decimal("0")


# ── admin-check mode: per-receipt moderation ────────────────────────────


@pytest.mark.asyncio
async def test_admin_check_pending_then_approve_completes(session):
    terminal, trader, payout = await _scaffold(session, receipts_to_close=1, receipt_auto=False)
    svc = PayoutService(session)

    out = await svc.add_receipt(str(payout.uuid), trader, None, AMOUNT_FIAT)
    assert out.status == PayoutStatus.AWAITING_CHECK
    assert await _bal(session, user_id=trader.id) == Decimal("0")    # not settled while pending
    r = (await _receipts(session, payout.id))[0]
    assert r.status == PayoutReceiptStatus.PENDING

    completed = await svc.admin_approve_receipt(r.id, admin_id=1)
    assert completed.status == PayoutStatus.COMPLETED
    assert await _bal(session, user_id=trader.id) == AMOUNT_USDT + TRADER_FEE


@pytest.mark.asyncio
async def test_admin_reject_returns_to_claimed_then_reupload(session):
    terminal, trader, payout = await _scaffold(session, receipts_to_close=1, receipt_auto=False)
    svc = PayoutService(session)

    await svc.add_receipt(str(payout.uuid), trader, None, AMOUNT_FIAT)
    r = (await _receipts(session, payout.id))[0]

    back = await svc.admin_reject_receipt(r.id, admin_id=1, reason="blurry")
    assert back.status == PayoutStatus.CLAIMED                       # re-upload that installment
    assert await _bal(session, user_id=trader.id) == Decimal("0")    # nothing settled
    assert (await session.get(PayoutReceipt, r.id)).status == PayoutReceiptStatus.REJECTED

    # Re-upload the full amount (rejected one freed the slot) → pending → approve → done.
    again = await svc.add_receipt(str(payout.uuid), trader, None, AMOUNT_FIAT)
    assert again.status == PayoutStatus.AWAITING_CHECK
    r2 = [x for x in await _receipts(session, payout.id) if x.status == PayoutReceiptStatus.PENDING][0]
    done = await svc.admin_approve_receipt(r2.id, admin_id=1)
    assert done.status == PayoutStatus.COMPLETED
    assert await _bal(session, user_id=trader.id) == AMOUNT_USDT + TRADER_FEE


# ── stale claim voids partial receipts ──────────────────────────────────


@pytest.mark.asyncio
async def test_admin_cancel_voids_pending_receipts(session):
    terminal, trader, payout = await _scaffold(session, receipts_to_close=2, receipt_auto=False)
    svc = PayoutService(session)
    await svc.add_receipt(str(payout.uuid), trader, None, Decimal("400"))   # PENDING → AWAITING_CHECK

    out = await svc.admin_cancel(str(payout.uuid), admin_id=1)
    assert out.status == PayoutStatus.CANCELED
    # Dead payout shows no live receipts; terminal refunded.
    assert all(r.status == PayoutReceiptStatus.REJECTED for r in await _receipts(session, payout.id))
    assert await _bal(session, payout_terminal_id=terminal.id) == FREEZE


@pytest.mark.asyncio
async def test_return_stale_claim_voids_receipts(session):
    terminal, trader, payout = await _scaffold(session, receipts_to_close=3, receipt_auto=True)
    svc = PayoutService(session)
    await svc.add_receipt(str(payout.uuid), trader, None, Decimal("400"))

    await svc.return_stale_claim(payout)

    refreshed = await session.get(Payout, payout.id)
    assert refreshed.status == PayoutStatus.CREATED
    assert refreshed.trader_id is None
    # Prior partial receipt voided → live sum is 0, slot freed for the next claimer.
    live = await svc.repository.sum_receipts(payout.id, statuses=svc._LIVE_RECEIPTS)
    assert live == Decimal("0")
    assert all(r.status == PayoutReceiptStatus.REJECTED for r in await _receipts(session, payout.id))
