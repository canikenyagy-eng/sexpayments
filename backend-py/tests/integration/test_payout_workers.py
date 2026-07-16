"""
Payout worker SELECTION queries (the rows the beat tasks act on):
  * list_expired      → expire_payouts_task
  * list_stale_claims → return_stale_payout_claims_task
  * list_holds_due    → release_payout_holds_task

The per-item transitions (refund / return-to-pool / hold release) are covered by
test_payout_money_flow; here we pin that the workers pick exactly the due rows.
"""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.payments import PaymentMethod
from app.common.enums.payouts import PayoutReceiptStatus, PayoutStatus
from app.common.enums.users import UserRole
from app.common.types import utcnow
from app.core.security import get_password_hash
from app.modules.payouts.models import Payout, PayoutReceipt, PayoutTerminal
from app.modules.payouts.repository import PayoutRepository
from app.modules.users.models import User

PAST = utcnow() - timedelta(minutes=5)
FUTURE = utcnow() + timedelta(hours=1)


async def _terminal(session) -> PayoutTerminal:
    u = User(username=f"ow_{uuid4().hex[:6]}", password=get_password_hash("p"), role=UserRole.MERCHANT,
             totp_enabled=False, is_blocked=False, use_shared_balance=True)
    session.add(u)
    await session.flush()
    s = uuid4().hex[:6]
    t = PayoutTerminal(user_id=u.id, name=f"PT-{s}", status=TerminalStatus.ENABLED, currency=Currency.RUB,
                       api_key=f"pk-{s}", api_secret=f"ps-{s}", commission_percent=Decimal("5"),
                       ttl_minutes=60, receipts_to_close=1)
    session.add(t)
    await session.flush()
    return t


async def _payout(session, *, terminal_id, status, expires_at=None, claim_expires_at=None,
                  trader_hold_until=None, hold_released_at=None, trader_id=None) -> Payout:
    p = Payout(uuid=uuid4(), external_id=f"e-{uuid4().hex[:8]}", payout_terminal_id=terminal_id,
               trader_id=trader_id, payment_method=PaymentMethod.SBP, amount=Decimal("1000"),
               currency=Currency.RUB, exchange_rate=Decimal("10"), amount_usdt=Decimal("100"),
               merchant_fee_usdt=Decimal("5"), req_holder="E", req_number="40817810099910000001",
               status=status, expires_at=expires_at, claim_expires_at=claim_expires_at,
               trader_hold_until=trader_hold_until, hold_released_at=hold_released_at)
    session.add(p)
    await session.flush()
    return p


@pytest.mark.asyncio
async def test_list_expired_picks_only_overdue_active(session):
    t = await _terminal(session)
    due = await _payout(session, terminal_id=t.id, status=PayoutStatus.CREATED, expires_at=PAST)
    due2 = await _payout(session, terminal_id=t.id, status=PayoutStatus.AWAITING_CHECK, expires_at=PAST)
    await _payout(session, terminal_id=t.id, status=PayoutStatus.CREATED, expires_at=FUTURE)        # not due
    await _payout(session, terminal_id=t.id, status=PayoutStatus.COMPLETED, expires_at=PAST)        # terminal state

    rows = await PayoutRepository(session).list_expired(utcnow())
    assert {p.id for p in rows} == {due.id, due2.id}


@pytest.mark.asyncio
async def test_list_stale_claims_picks_only_lapsed(session):
    t = await _terminal(session)
    stale = await _payout(session, terminal_id=t.id, status=PayoutStatus.CLAIMED, claim_expires_at=PAST)
    await _payout(session, terminal_id=t.id, status=PayoutStatus.CLAIMED, claim_expires_at=FUTURE)   # fresh
    await _payout(session, terminal_id=t.id, status=PayoutStatus.CREATED, claim_expires_at=PAST)     # not claimed

    rows = await PayoutRepository(session).list_stale_claims(utcnow())
    assert {p.id for p in rows} == {stale.id}


@pytest.mark.asyncio
async def test_list_expired_skips_payouts_with_live_receipts(session):
    """Money safety: an expired payout the trader started paying (has a live
    receipt) must NOT be auto-expired; one with only a REJECTED receipt still is."""
    t = await _terminal(session)
    tr = User(username=f"tr_{uuid4().hex[:6]}", password=get_password_hash("p"), role=UserRole.TRADER,
              totp_enabled=False, is_blocked=False, use_shared_balance=True)
    session.add(tr)
    await session.flush()

    with_live = await _payout(session, terminal_id=t.id, status=PayoutStatus.CLAIMED, expires_at=PAST, trader_id=tr.id)
    session.add(PayoutReceipt(payout_id=with_live.id, trader_id=tr.id, amount=Decimal("400"),
                              file="x", status=PayoutReceiptStatus.APPROVED))
    only_rejected = await _payout(session, terminal_id=t.id, status=PayoutStatus.CLAIMED, expires_at=PAST, trader_id=tr.id)
    session.add(PayoutReceipt(payout_id=only_rejected.id, trader_id=tr.id, amount=Decimal("400"),
                              file="x", status=PayoutReceiptStatus.REJECTED))
    no_receipt = await _payout(session, terminal_id=t.id, status=PayoutStatus.CREATED, expires_at=PAST)
    await session.flush()

    ids = {p.id for p in await PayoutRepository(session).list_expired(utcnow())}
    assert with_live.id not in ids               # protected — trader is paying
    assert only_rejected.id in ids               # rejected doesn't protect
    assert no_receipt.id in ids


@pytest.mark.asyncio
async def test_list_holds_due_picks_only_unreleased_elapsed(session):
    t = await _terminal(session)
    due = await _payout(session, terminal_id=t.id, status=PayoutStatus.COMPLETED, trader_hold_until=PAST)
    await _payout(session, terminal_id=t.id, status=PayoutStatus.COMPLETED, trader_hold_until=FUTURE)  # not elapsed
    await _payout(session, terminal_id=t.id, status=PayoutStatus.COMPLETED, trader_hold_until=PAST,
                  hold_released_at=utcnow())                                                            # already released

    rows = await PayoutRepository(session).list_holds_due(utcnow())
    assert {p.id for p in rows} == {due.id}
