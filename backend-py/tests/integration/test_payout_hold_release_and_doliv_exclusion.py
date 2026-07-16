"""
Integration tests for two money-critical payout invariants against a REAL
in-memory ledger:

  (#4) Payout hold-release IDEMPOTENCY — ``PayoutService.release_hold`` moves the
       held trader earnings (amount + trader fee) ESCROW → WORK *exactly once*,
       even when called twice in a row. The second call must be a no-op (the
       app-level ``hold_released_at`` re-check under the row lock is what the
       PostgreSQL ``SELECT ... FOR UPDATE`` protects under real concurrency; on
       SQLite the lock is a no-op, so we exercise the SEQUENTIAL re-check by
       calling release_hold twice).

  (#5) Долив EXCLUSION from every payout query — a долив (``is_doliv=True``) lives
       in its own DolivService pool/sweep and must NEVER surface in the payout
       terminal queries. Each of list_expired / list_stale_claims / list_holds_due
       / list_pool / list_for_trader / list_admin / count_admin now carries
       ``Payout.is_doliv.is_(False)``; we pin that each EXCLUDES the долив and
       INCLUDES an otherwise-identical normal payout set up to match that query.

Self-contained: own local builders + ``_bal`` (mirrors test_payout_money_flow).
"""
from datetime import timedelta
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
from app.common.types import utcnow
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.payouts.models import Payout, PayoutTerminal
from app.modules.payouts.repository import PayoutRepository
from app.modules.payouts.service import PayoutService
from app.modules.traders.models import Trader
from app.modules.users.models import User

AMOUNT = Decimal("100.0000")        # amount_usdt sent to the end-user
MERCHANT_FEE = Decimal("5.0000")    # commission charged to the merchant
TRADER_FEE = Decimal("2.0000")      # trader reward
FREEZE = AMOUNT + MERCHANT_FEE      # 105 — frozen at creation
EARNINGS = AMOUNT + TRADER_FEE      # 102 — what the hold release moves ESCROW→WORK

PAST = utcnow() - timedelta(minutes=5)
FUTURE = utcnow() + timedelta(hours=1)


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


async def _mk_balance(session, amount, *, user_id=None, payout_terminal_id=None,
                      is_system=False, btype=BalanceType.WORK) -> Balance:
    b = Balance(user_id=user_id, payout_terminal_id=payout_terminal_id, is_system=is_system,
                type=btype, currency=Currency.USDT, amount=Decimal(amount))
    session.add(b)
    await session.flush()
    return b


async def _mk_payout(
    session, *, terminal_id=None, trader_id=None, status=PayoutStatus.CREATED,
    is_doliv=False, refill_requisite_id=None, requester_trader_id=None,
    expires_at=None, claim_expires_at=None,
    trader_hold_until=None, hold_released_at=None,
) -> Payout:
    p = Payout(
        uuid=uuid4(), external_id=f"ext-{uuid4().hex[:8]}", payout_terminal_id=terminal_id,
        trader_id=trader_id, payment_method=PaymentMethod.SBP,
        amount=Decimal("1000.00"), currency=Currency.RUB, exchange_rate=Decimal("10"),
        amount_usdt=AMOUNT, merchant_fee_usdt=MERCHANT_FEE,
        trader_fee_usdt=(TRADER_FEE if trader_id else None),
        req_holder="End User", req_number="40817810099910000001", status=status,
        is_doliv=is_doliv, refill_requisite_id=refill_requisite_id,
        requester_trader_id=requester_trader_id,
        expires_at=expires_at, claim_expires_at=claim_expires_at,
        trader_hold_until=trader_hold_until, hold_released_at=hold_released_at,
    )
    session.add(p)
    await session.flush()
    return p


async def _bal(session, *, user_id=None, payout_terminal_id=None, is_system=False,
               btype=BalanceType.WORK) -> Decimal:
    stmt = select(Balance).where(Balance.type == btype, Balance.currency == Currency.USDT)
    if is_system:
        stmt = stmt.where(Balance.is_system.is_(True))
    elif user_id is not None:
        stmt = stmt.where(Balance.user_id == user_id)
    elif payout_terminal_id is not None:
        stmt = stmt.where(Balance.payout_terminal_id == payout_terminal_id)
    row = (await session.execute(stmt)).scalars().first()
    return row.amount if row else Decimal("0")


async def _ledger_legs(session, payout_id: int) -> list[LedgerEntry]:
    """Every ledger leg referencing this payout (reference_id == f'payout:{id}')."""
    stmt = select(LedgerEntry).where(LedgerEntry.reference_id == f"payout:{payout_id}")
    return list((await session.execute(stmt)).scalars().all())


async def _setup_completed_hold(session):
    """A terminal whose ESCROW holds the freeze + a held trader; complete the
    payout (CLAIMED → COMPLETED) so the trader's earnings are parked in ESCROW
    with trader_hold_until set. Returns (terminal, trader, completed_payout)."""
    owner = await _mk_user(session, username=f"ow_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    terminal = await _mk_terminal(session, owner_id=owner.id, suffix=uuid4().hex[:6])
    await _mk_balance(session, FREEZE, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW)
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    await _mk_trader_profile(session, user_id=trader.id, hold_hours=24)
    payout = await _mk_payout(session, terminal_id=terminal.id, trader_id=trader.id,
                              status=PayoutStatus.CLAIMED)
    completed = await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)
    return terminal, trader, completed


# ══════════════════════════════════════════════════════════════════════
# #4 — hold-release: correct behaviour + idempotency
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_complete_with_hold_parks_earnings_then_release_moves_to_work(session):
    """CORRECT: complete with hold_hours>0 → trader earnings (amount+fee) in
    ESCROW; release_hold once → ESCROW → WORK exactly amount+fee."""
    terminal, trader, completed = await _setup_completed_hold(session)

    # Parked: trader earnings sit in ESCROW, WORK is empty, hold timer set, not yet released.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == EARNINGS
    assert await _bal(session, user_id=trader.id) == Decimal("0")
    assert completed.trader_hold_until is not None
    assert completed.hold_released_at is None
    # Terminal ESCROW fully drained by the settle (commission to system, amount to trader hold).
    assert await _bal(session, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW) == Decimal("0")

    await PayoutService(session).release_hold(completed)

    # Released: ESCROW → WORK, value conserved (102 in == 102 out).
    assert await _bal(session, user_id=trader.id) == EARNINGS
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    refreshed = await session.get(Payout, completed.id)
    assert refreshed.hold_released_at is not None


@pytest.mark.asyncio
async def test_release_hold_twice_moves_money_exactly_once(session):
    """ADVERSARIAL: release_hold called TWICE in a row → money moves ONCE.
    Final WORK == amount+fee, ESCROW == 0, hold_released_at set, and the ledger
    gained exactly ONE release leg (a double-release would show two)."""
    terminal, trader, completed = await _setup_completed_hold(session)

    svc = PayoutService(session)
    legs_before = len(await _ledger_legs(session, completed.id))

    await svc.release_hold(completed)
    first_work = await _bal(session, user_id=trader.id)
    legs_after_first = len(await _ledger_legs(session, completed.id))
    refreshed_once = await session.get(Payout, completed.id)
    released_stamp = refreshed_once.hold_released_at

    # Second call on the SAME (now-released) payout object → must no-op.
    await svc.release_hold(completed)
    second_work = await _bal(session, user_id=trader.id)
    legs_after_second = len(await _ledger_legs(session, completed.id))
    refreshed_twice = await session.get(Payout, completed.id)

    # Money moved exactly once.
    assert first_work == EARNINGS
    assert second_work == EARNINGS                                  # NOT 2 * EARNINGS
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    # The release added exactly one ledger leg; the 2nd call added none.
    assert legs_after_first == legs_before + 1
    assert legs_after_second == legs_after_first
    # Exactly one ORDER_PAYOUT hold-release leg ESCROW→WORK for this payout.
    release_legs = [
        e for e in await _ledger_legs(session, completed.id)
        if e.reference_type == LedgerReferenceType.ORDER_PAYOUT and e.amount == EARNINGS
    ]
    assert len(release_legs) == 1
    # hold_released_at set once and unchanged by the 2nd call.
    assert released_stamp is not None
    assert refreshed_twice.hold_released_at == released_stamp


@pytest.mark.asyncio
async def test_release_hold_reread_from_db_still_no_double_release(session):
    """ADVERSARIAL: a worker that re-fetches the payout fresh (a separate ORM
    object with hold_released_at already persisted) must ALSO no-op — the guard
    is on the persisted value, not object identity."""
    terminal, trader, completed = await _setup_completed_hold(session)
    await PayoutService(session).release_hold(completed)
    assert await _bal(session, user_id=trader.id) == EARNINGS

    # Fresh object, as a second worker run would load it: detach the identity-
    # mapped row and re-SELECT so hold_released_at comes straight from the DB.
    completed_id = completed.id
    session.expunge_all()
    reloaded = await session.get(Payout, completed_id)
    assert reloaded.hold_released_at is not None        # persisted, not just in-memory
    await PayoutService(session).release_hold(reloaded)

    assert await _bal(session, user_id=trader.id) == EARNINGS      # still once
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")


@pytest.mark.asyncio
async def test_release_hold_noop_when_no_hold_was_set(session):
    """ADVERSARIAL: a COMPLETED payout with NO hold (trader_hold_until is None)
    must never move money on release_hold — guards against draining an empty
    trader ESCROW into WORK out of thin air."""
    owner = await _mk_user(session, username=f"ow_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    terminal = await _mk_terminal(session, owner_id=owner.id, suffix=uuid4().hex[:6])
    await _mk_balance(session, FREEZE, payout_terminal_id=terminal.id, btype=BalanceType.ESCROW)
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    await _mk_trader_profile(session, user_id=trader.id, hold_hours=0)          # no hold
    payout = await _mk_payout(session, terminal_id=terminal.id, trader_id=trader.id,
                              status=PayoutStatus.CLAIMED)
    completed = await PayoutService(session).change_status(payout, PayoutStatus.COMPLETED)

    # No hold → earnings landed straight in WORK.
    assert completed.trader_hold_until is None
    assert await _bal(session, user_id=trader.id) == EARNINGS
    work_before = await _bal(session, user_id=trader.id)
    escrow_before = await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW)

    await PayoutService(session).release_hold(completed)           # must be a no-op

    assert await _bal(session, user_id=trader.id) == work_before
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == escrow_before


# ══════════════════════════════════════════════════════════════════════
# #5 — долив exclusion from every payout query
# ══════════════════════════════════════════════════════════════════════
#
# Each test builds ONE normal payout (set up to MATCH the query) and ONE долив
# (is_doliv=True + refill_requisite_id) in the SAME matching status/timestamps,
# then asserts the query INCLUDES the normal and EXCLUDES the долив.


async def _doliv_setup(session, *, normal_status, doliv_status=None, **payout_kwargs):
    """Owner + terminal + a normal payout and a parallel долив, both in matching
    states. Returns (terminal, normal, doliv)."""
    owner = await _mk_user(session, username=f"ow_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    terminal = await _mk_terminal(session, owner_id=owner.id, suffix=uuid4().hex[:6])
    requester = await _mk_user(session, username=f"rq_{uuid4().hex[:6]}")
    normal = await _mk_payout(session, terminal_id=terminal.id, status=normal_status, **payout_kwargs)
    # A долив has NO terminal (payout_terminal_id is NULL) and carries the refill
    # target; give it the same status/timestamps so ONLY is_doliv distinguishes it.
    doliv = await _mk_payout(
        session, terminal_id=None, status=(doliv_status or normal_status),
        is_doliv=True, refill_requisite_id=4242, requester_trader_id=requester.id,
        **payout_kwargs,
    )
    return terminal, normal, doliv


@pytest.mark.asyncio
async def test_list_expired_excludes_doliv(session):
    terminal, normal, doliv = await _doliv_setup(
        session, normal_status=PayoutStatus.CREATED, expires_at=PAST,
    )
    ids = {p.id for p in await PayoutRepository(session).list_expired(utcnow())}
    assert normal.id in ids
    assert doliv.id not in ids


@pytest.mark.asyncio
async def test_list_stale_claims_excludes_doliv(session):
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    terminal, normal, doliv = await _doliv_setup(
        session, normal_status=PayoutStatus.CLAIMED, claim_expires_at=PAST,
        trader_id=trader.id,
    )
    ids = {p.id for p in await PayoutRepository(session).list_stale_claims(utcnow())}
    assert normal.id in ids
    assert doliv.id not in ids


@pytest.mark.asyncio
async def test_list_holds_due_excludes_doliv(session):
    terminal, normal, doliv = await _doliv_setup(
        session, normal_status=PayoutStatus.COMPLETED,
        trader_hold_until=PAST, hold_released_at=None,
    )
    ids = {p.id for p in await PayoutRepository(session).list_holds_due(utcnow())}
    assert normal.id in ids
    assert doliv.id not in ids


@pytest.mark.asyncio
async def test_list_pool_excludes_doliv(session):
    """The claimable pool (CREATED) must never surface a долив — доливы have
    their own DolivService pool."""
    terminal, normal, doliv = await _doliv_setup(
        session, normal_status=PayoutStatus.CREATED, expires_at=FUTURE,
    )
    rows = await PayoutRepository(session).list_pool(terminal_ids=None)
    ids = {p.id for p in rows}
    assert normal.id in ids
    assert doliv.id not in ids


@pytest.mark.asyncio
async def test_list_for_trader_excludes_doliv(session):
    """The trader's own-payout query must exclude доливы they were assigned to as
    claimer (доливщик) — those belong to the долив cabinet, not the payout one."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    owner = await _mk_user(session, username=f"ow_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    terminal = await _mk_terminal(session, owner_id=owner.id, suffix=uuid4().hex[:6])
    normal = await _mk_payout(session, terminal_id=terminal.id, trader_id=trader.id,
                              status=PayoutStatus.COMPLETED)
    doliv = await _mk_payout(session, terminal_id=None, trader_id=trader.id,
                             status=PayoutStatus.COMPLETED, is_doliv=True,
                             refill_requisite_id=4242)

    rows = await PayoutRepository(session).list_for_trader(trader.id)
    ids = {p.id for p in rows}
    assert normal.id in ids
    assert doliv.id not in ids


@pytest.mark.asyncio
async def test_list_admin_and_count_admin_exclude_doliv(session):
    """Admin payout listing + count both filter доливы out (they have their own
    admin tooling)."""
    terminal, normal, doliv = await _doliv_setup(
        session, normal_status=PayoutStatus.CREATED, expires_at=FUTURE,
    )
    repo = PayoutRepository(session)

    rows = await repo.list_admin()
    ids = {p.id for p in rows}
    assert normal.id in ids
    assert doliv.id not in ids

    # count_admin must agree with list_admin (no долив double-count).
    assert await repo.count_admin() == len(rows)
    # And filtered by the normal payout's terminal: exactly the one normal row.
    assert await repo.count_admin(terminal_id=terminal.id) == 1
    scoped = await repo.list_admin(terminal_id=terminal.id)
    assert {p.id for p in scoped} == {normal.id}


@pytest.mark.asyncio
async def test_doliv_present_in_table_but_invisible_to_all_payout_queries(session):
    """Sanity cross-check: a долив EXISTS in the payouts table (a direct SELECT
    finds it) yet is absent from EVERY payout-side query at once — proving the
    exclusion is a query filter, not a missing row."""
    trader = await _mk_user(session, username=f"tr_{uuid4().hex[:6]}")
    owner = await _mk_user(session, username=f"ow_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    terminal = await _mk_terminal(session, owner_id=owner.id, suffix=uuid4().hex[:6])
    doliv = await _mk_payout(
        session, terminal_id=None, trader_id=trader.id, status=PayoutStatus.CREATED,
        is_doliv=True, refill_requisite_id=4242, expires_at=PAST,
        claim_expires_at=PAST, trader_hold_until=PAST,
    )

    # The row really is in the table.
    direct = (await session.execute(select(Payout).where(Payout.id == doliv.id))).scalars().first()
    assert direct is not None and direct.is_doliv is True

    repo = PayoutRepository(session)
    now = utcnow()
    # Build a COMPLETED variant id-set too for list_holds_due coverage.
    doliv_completed = await _mk_payout(
        session, terminal_id=None, trader_id=trader.id, status=PayoutStatus.COMPLETED,
        is_doliv=True, refill_requisite_id=4242, trader_hold_until=PAST, hold_released_at=None,
    )

    assert doliv.id not in {p.id for p in await repo.list_expired(now)}
    assert doliv.id not in {p.id for p in await repo.list_stale_claims(now)}
    assert doliv_completed.id not in {p.id for p in await repo.list_holds_due(now)}
    assert doliv.id not in {p.id for p in await repo.list_pool(terminal_ids=None)}
    assert doliv.id not in {p.id for p in await repo.list_for_trader(trader.id)}
    assert doliv.id not in {p.id for p in await repo.list_admin()}
    assert doliv_completed.id not in {p.id for p in await repo.list_admin()}
