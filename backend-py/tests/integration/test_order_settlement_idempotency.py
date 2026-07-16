"""
Integration tests for ORDER SETTLEMENT IDEMPOTENCY — the core
double-completion fix — against a REAL in-memory ledger (no mocked finance).

The fix (see ``OrderService.complete_order`` + ``change_status``):
  * ``complete_order`` loads the order ``FOR UPDATE`` (``get_for_update``) and
    short-circuits with ``if order.status == SUCCESS: return order`` BEFORE the
    completable check — so a second confirm never settles twice.
  * ``change_status`` row-locks the order (``lock_status``), re-reads the
    COMMITTED status, and ``if new_status == old_status: return order`` — so a
    re-confirm of an already-terminal order is a clean no-op (no second
    settlement) and the transition graph still rejects illegal terminal→terminal
    moves.

SQLite caveat: ``SELECT … FOR UPDATE`` is a NO-OP on the single shared
StaticPool connection, so these tests do NOT exercise real row-lock concurrency.
Instead they prove the APPLICATION-LEVEL status re-check that the lock protects
under real Postgres concurrency: call the settlement path TWICE IN A ROW and
assert money moved EXACTLY ONCE — exact Decimal balances AND ledger-leg counts.

PAYIN settlement (``FinanceService.complete_order``) moves, for amount=100,
fee=5, trader_fee=2:
  * trader ESCROW(100) → merchant WORK            (ORDER_PAYIN settle leg)
  * merchant WORK → system WORK  fee=5            (SYSTEM_COMMISSION)
  * system WORK → trader WORK    reward=2         (TRADER_REWARD)
→ merchant 95, trader 2, system 3, escrow 0.

The order's freeze leg (CREATED/PENDING) is ALSO an ORDER_PAYIN entry, so a
correctly-settled order has EXACTLY 2 ORDER_PAYIN legs (freeze + settle); a
double-settle bug shows as 3+ ORDER_PAYIN legs (or doubled balances).
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.users import UserRole
from app.core.exceptions import ConflictException, ForbiddenException, NotFoundException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.orders.service import OrderService
from app.modules.users.models import User

# Canonical money shape (mirrors the dispute / admin templates).
AMOUNT = Decimal("100.0000")        # trader collateral / settled amount_usdt
FEE = Decimal("5.0000")             # merchant commission → system
TRADER_FEE = Decimal("2.0000")     # trader reward
NET = AMOUNT - FEE                  # 95 — what the merchant keeps on success
SYSTEM_PROFIT = FEE - TRADER_FEE    # 3 — platform profit after rewarding trader


@pytest.fixture(autouse=True)
def _stub_celery():
    """complete_order / change_status fire the merchant webhook + selector
    feedback through Celery — stub the broker so the test never reaches a real
    one. ``create=True`` so the patch holds even if the attr isn't bound."""
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.modules.orders.service.celery_app", celery, create=True):
        yield celery


# ── builders (self-contained, copied shape from the canonical templates) ──


async def _mk_user(session, *, username, role=UserRole.TRADER) -> User:
    u = User(
        username=username,
        password=get_password_hash("pass12345"),
        role=role,
        totp_enabled=False,
        is_blocked=False,
        use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_merchant(session, *, user_id, suffix) -> Merchant:
    m = Merchant(
        user_id=user_id,
        name=f"M-{suffix}",
        status=TerminalStatus.ENABLED,
        currency=Currency.RUB,
        api_key=f"key-{suffix}",
        api_secret=f"secret-{suffix}",
        fees={PaymentMethod.SBP.value: 5},
    )
    session.add(m)
    await session.flush()
    return m


async def _mk_balance(session, amount, *, user_id=None, merchant_id=None, is_system=False, btype=BalanceType.WORK) -> Balance:
    b = Balance(
        user_id=user_id,
        merchant_id=merchant_id,
        is_system=is_system,
        type=btype,
        currency=Currency.USDT,
        amount=Decimal(amount),
    )
    session.add(b)
    await session.flush()
    return b


async def _mk_order(session, *, merchant_id, trader_id, status) -> Order:
    o = Order(
        uuid=uuid4(),
        external_id=f"ext-{uuid4().hex[:8]}",
        merchant_id=merchant_id,
        trader_id=trader_id,
        direction=PaymentDirection.PAYIN,
        payment_method=PaymentMethod.SBP,
        amount=Decimal("1000.00"),
        currency=Currency.RUB,
        exchange_rate=Decimal("10"),
        amount_usdt=AMOUNT,
        fee_usdt=FEE,
        trader_fee_usdt=TRADER_FEE,
        profit_usdt=AMOUNT - FEE,
        status=status,
    )
    session.add(o)
    await session.flush()
    return o


async def _bal(session, *, user_id=None, merchant_id=None, is_system=False, btype=BalanceType.WORK) -> Decimal:
    """Current amount of a balance, 0 if it was never created."""
    stmt = select(Balance).where(Balance.type == btype, Balance.currency == Currency.USDT)
    if is_system:
        stmt = stmt.where(Balance.is_system.is_(True))
    elif user_id is not None:
        stmt = stmt.where(Balance.user_id == user_id)
    elif merchant_id is not None:
        stmt = stmt.where(Balance.merchant_id == merchant_id)
    row = (await session.execute(stmt)).scalars().first()
    return row.amount if row else Decimal("0")


async def _legs(session, order_id, ref_type) -> list[LedgerEntry]:
    """All ledger entries for an order with a given reference type."""
    stmt = select(LedgerEntry).where(
        LedgerEntry.reference_id == str(order_id),
        LedgerEntry.reference_type == ref_type,
    )
    return list((await session.execute(stmt)).scalars().all())


async def _setup(session, *, order_status):
    """Trader + merchant + a PAYIN order with the collateral already frozen in
    trader ESCROW (the PENDING/RECEIPT_UPLOADED pre-settlement state).

    To make the ledger-leg assertion realistic, the FREEZE leg is also written:
    a real freeze (work→escrow) is recorded so a correctly-settled order ends
    with EXACTLY 2 ORDER_PAYIN legs (freeze + settle)."""
    trader = await _mk_user(session, username=f"trader_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])

    # Seed the trader with WORK, then freeze the collateral through the REAL
    # finance freeze leg so the ORDER_PAYIN freeze entry exists in the ledger.
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.WORK)
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=order_status)

    from app.modules.finance.service import FinanceService
    await FinanceService(session).create_order(order=order, trader=trader)
    # Post-freeze: collateral sits in trader ESCROW, WORK drained.
    return trader, merchant, order


def _total(*amounts) -> Decimal:
    return sum(amounts, Decimal("0"))


# ── CORRECT BEHAVIOUR: single completion ────────────────────────────────


@pytest.mark.asyncio
async def test_complete_once_settles_exact_balances_and_two_payin_legs(session):
    """A single ``complete_order`` settles the frozen collateral: merchant 95,
    trader reward 2, system 3, escrow 0 — and the ledger has exactly the
    freeze+settle ORDER_PAYIN legs plus one commission + one reward leg."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.RECEIPT_UPLOADED)
    # Sanity: collateral frozen, nothing settled yet.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT
    assert await _bal(session, user_id=trader.id) == Decimal("0")

    out = await OrderService(session).complete_order(trader=trader, order_id=order.id)

    assert out.status == OrderStatus.SUCCESS
    assert await _bal(session, merchant_id=merchant.id) == NET                      # 95
    assert await _bal(session, user_id=trader.id) == TRADER_FEE                     # 2
    assert await _bal(session, is_system=True) == SYSTEM_PROFIT                     # 3
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")

    # Ledger shape: freeze + settle ⇒ 2 ORDER_PAYIN; 1 commission; 1 reward.
    assert len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)) == 2
    assert len(await _legs(session, order.id, LedgerReferenceType.SYSTEM_COMMISSION)) == 1
    assert len(await _legs(session, order.id, LedgerReferenceType.TRADER_REWARD)) == 1
    # Value conserved: the 100 collateral ends as 95 + 2 + 3.
    assert NET + TRADER_FEE + SYSTEM_PROFIT == AMOUNT


# ── ADVERSARIAL 1: double complete_order → money moves ONCE ──────────────


@pytest.mark.asyncio
async def test_complete_order_twice_in_a_row_moves_money_exactly_once(session):
    """The double-completion fix: calling ``complete_order`` TWICE on the same
    order yields balances IDENTICAL to a single completion. The second call hits
    the ``status == SUCCESS`` short-circuit → no second settlement, no doubled
    balances, NO extra ledger legs."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.PENDING)
    svc = OrderService(session)

    first = await svc.complete_order(trader=trader, order_id=order.id)
    assert first.status == OrderStatus.SUCCESS
    # Snapshot the post-first-settle balances + leg counts.
    bals_after_first = (
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, user_id=trader.id),
        await _bal(session, is_system=True),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
    )
    legs_after_first = (
        len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)),
        len(await _legs(session, order.id, LedgerReferenceType.SYSTEM_COMMISSION)),
        len(await _legs(session, order.id, LedgerReferenceType.TRADER_REWARD)),
    )

    # SECOND confirm (double-click / retry / cabinet+bot race) — must be a no-op.
    second = await svc.complete_order(trader=trader, order_id=order.id)
    assert second.status == OrderStatus.SUCCESS

    bals_after_second = (
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, user_id=trader.id),
        await _bal(session, is_system=True),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
    )
    legs_after_second = (
        len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)),
        len(await _legs(session, order.id, LedgerReferenceType.SYSTEM_COMMISSION)),
        len(await _legs(session, order.id, LedgerReferenceType.TRADER_REWARD)),
    )

    # Money moved EXACTLY once.
    assert bals_after_second == bals_after_first == (NET, TRADER_FEE, SYSTEM_PROFIT, Decimal("0"))
    # No extra ledger legs (2 ORDER_PAYIN = freeze+settle, NOT 3).
    assert legs_after_second == legs_after_first == (2, 1, 1)


@pytest.mark.asyncio
async def test_complete_order_three_times_still_settles_once(session):
    """Hammer it: three sequential confirms still settle exactly once. Guards
    against an off-by-one in the status re-check (only the FIRST should move
    money; the 2nd and 3rd are no-ops)."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.RECEIPT_UPLOADED)
    svc = OrderService(session)

    for _ in range(3):
        out = await svc.complete_order(trader=trader, order_id=order.id)
        assert out.status == OrderStatus.SUCCESS

    assert await _bal(session, merchant_id=merchant.id) == NET
    assert await _bal(session, user_id=trader.id) == TRADER_FEE
    assert await _bal(session, is_system=True) == SYSTEM_PROFIT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)) == 2


# ── ADVERSARIAL 2: change_status to the SAME status → no-op ──────────────


@pytest.mark.asyncio
async def test_change_status_to_same_status_moves_no_money(session):
    """``change_status`` to the status the order already holds is a pure no-op
    (the ``new_status == old_status`` guard) — zero money moved, ledger
    untouched (only the freeze leg from setup exists)."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.PENDING)
    svc = OrderService(session)

    out = await svc.change_status(order, OrderStatus.PENDING, audit_action=None)

    assert out.status == OrderStatus.PENDING
    # Untouched: collateral still wholly in ESCROW, no settlement.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT
    assert await _bal(session, user_id=trader.id) == Decimal("0")
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")
    # Only the single freeze leg exists — change_status created NO new entry.
    assert len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)) == 1


# ── ADVERSARIAL 3: SUCCESS → SUCCESS re-confirm → no second settlement ───


@pytest.mark.asyncio
async def test_change_status_success_to_success_is_idempotent_no_second_settle(session):
    """Re-confirming an already-SUCCESS order via ``change_status(SUCCESS)``
    returns idempotently — the same-status guard fires before ANY finance call,
    so there is no second settlement and balances are unchanged."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.RECEIPT_UPLOADED)
    svc = OrderService(session)

    # First settle.
    settled = await svc.change_status(order, OrderStatus.SUCCESS, audit_action=None)
    assert settled.status == OrderStatus.SUCCESS
    assert await _bal(session, merchant_id=merchant.id) == NET

    # Re-confirm SUCCESS→SUCCESS: idempotent no-op.
    fresh = await session.get(Order, order.id)
    again = await svc.change_status(fresh, OrderStatus.SUCCESS, audit_action=None)
    assert again.status == OrderStatus.SUCCESS

    # No doubling.
    assert await _bal(session, merchant_id=merchant.id) == NET
    assert await _bal(session, user_id=trader.id) == TRADER_FEE
    assert await _bal(session, is_system=True) == SYSTEM_PROFIT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)) == 2
    assert len(await _legs(session, order.id, LedgerReferenceType.SYSTEM_COMMISSION)) == 1
    assert len(await _legs(session, order.id, LedgerReferenceType.TRADER_REWARD)) == 1


# ── ADVERSARIAL 4: terminal → terminal illegal transition still raises ───


@pytest.mark.asyncio
async def test_illegal_terminal_to_terminal_transition_raises_and_moves_no_money(session):
    """The transition graph still rejects an illegal terminal→terminal move
    (SUCCESS → CANCELED is NOT in ``_ALLOWED_TRANSITIONS[SUCCESS]`` without
    ``force``). The same-status short-circuit does NOT swallow it (different
    target), and no money moves on the rejected attempt."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.RECEIPT_UPLOADED)
    svc = OrderService(session)

    settled = await svc.change_status(order, OrderStatus.SUCCESS, audit_action=None)
    assert settled.status == OrderStatus.SUCCESS
    snapshot = (
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, user_id=trader.id),
        await _bal(session, is_system=True),
    )

    fresh = await session.get(Order, order.id)
    with pytest.raises(ConflictException, match="Illegal order transition"):
        await svc.change_status(fresh, OrderStatus.CANCELED, audit_action=None)

    # Order untouched, no money moved by the rejected transition.
    after = await session.get(Order, order.id)
    assert after.status == OrderStatus.SUCCESS
    assert (
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, user_id=trader.id),
        await _bal(session, is_system=True),
    ) == snapshot


@pytest.mark.asyncio
async def test_complete_order_on_failed_order_rejected_not_settled(session):
    """A FAILED order is not COMPLETABLE: ``complete_order`` must raise
    (status != SUCCESS so the idempotent short-circuit doesn't apply, and FAILED
    is not in COMPLETABLE_STATUSES). No accidental settlement of a released
    order."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.PENDING)
    svc = OrderService(session)
    # Release the collateral (PENDING → FAILED).
    await svc.change_status(order, OrderStatus.FAILED, audit_action=None)
    assert await _bal(session, user_id=trader.id) == AMOUNT       # back in WORK

    fresh = await session.get(Order, order.id)
    with pytest.raises(ConflictException, match="cannot be completed"):
        await svc.complete_order(trader=trader, order_id=fresh.id)

    # Nothing settled to the merchant; collateral stays released to the trader.
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")
    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, is_system=True) == Decimal("0")


# ── ADVERSARIAL 5: ownership guard + double-settle via the public API ────


@pytest.mark.asyncio
async def test_complete_order_wrong_trader_forbidden_no_money(session):
    """Only the assigned trader may complete the order. A different trader is
    rejected (``ForbiddenException``) and no settlement occurs."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.PENDING)
    intruder = await _mk_user(session, username=f"intruder_{uuid4().hex[:6]}", role=UserRole.TRADER)
    svc = OrderService(session)

    with pytest.raises(ForbiddenException):
        await svc.complete_order(trader=intruder, order_id=order.id)

    # Untouched: collateral still frozen, nothing settled.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")


@pytest.mark.asyncio
async def test_complete_order_missing_order_raises(session):
    """``complete_order`` on an unknown id raises NotFoundException — never
    silently mints money."""
    trader = await _mk_user(session, username=f"t_{uuid4().hex[:6]}", role=UserRole.TRADER)
    with pytest.raises(NotFoundException):
        await OrderService(session).complete_order(trader=trader, order_id=999_999)


# ── value conservation across a double-settle attempt ────────────────────


@pytest.mark.asyncio
async def test_total_value_conserved_under_double_complete(session):
    """Across a double ``complete_order``, the grand total of every bucket stays
    EXACTLY the original 100 collateral — no money created (the bug) or
    destroyed."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.RECEIPT_UPLOADED)
    svc = OrderService(session)

    before = _total(
        await _bal(session, user_id=trader.id),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, is_system=True),
    )
    assert before == AMOUNT

    await svc.complete_order(trader=trader, order_id=order.id)
    await svc.complete_order(trader=trader, order_id=order.id)  # double

    after = _total(
        await _bal(session, user_id=trader.id),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, is_system=True),
    )
    assert after == before == AMOUNT
