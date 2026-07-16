"""
Integration tests for PAYIN FAILED / CANCELED **release** — the ledger SHAPE of
an unsuccessful payin and the idempotency of a double-release — against a REAL
in-memory ledger (no mocked finance).

A payin order freezes the trader's collateral WORK → ESCROW on assignment
(``FinanceService.create_order`` → 1 ORDER_PAYIN freeze leg). When the order
ends UNSUCCESSFULLY it must be *released*, not settled:

  * ``OrderService.fail_order``     → ``change_status(FAILED)``  → ``cancel_order``
  * ``OrderService.cancel_order``   → ``change_status(CANCELED)`` → ``cancel_order``

``FinanceService.cancel_order`` moves trader ESCROW → WORK (1 ORDER_PAYIN release
leg, opposite direction to the freeze). So a correctly released payin order ends
with:
  * EXACTLY 2 ORDER_PAYIN legs (freeze + release, opposite directions),
  * ZERO SYSTEM_COMMISSION / TRADER_REWARD / TEAMLEAD_REWARD legs (no settlement),
  * trader WORK back to the FULL collateral, ESCROW drained to 0,
  * merchant + system untouched,
  * total value conserved (the 100 collateral never leaves the trader).

SQLite caveat (mirrors the settlement-idempotency template): ``SELECT … FOR
UPDATE`` and PG locks are NO-OPS on the shared StaticPool connection, so these
tests do NOT exercise real row-lock concurrency. Instead they prove the
APPLICATION-LEVEL guards the lock protects under real Postgres concurrency: call
the release path TWICE IN A ROW and assert money moved EXACTLY ONCE — exact
Decimal balances AND ledger-leg counts AND value conservation.

The second fail/cancel is a same-status no-op: ``change_status`` re-reads the
committed status and returns early on ``new_status == old_status`` (FAILED→FAILED
/ CANCELED→CANCELED), so the release runs only once (leg count stays 2, the
trader's WORK is not doubled).

Edge cases:
  * Canceling a CREATED order (collateral never frozen) releases NOTHING — the
    ESCROW(0)→WORK transfer raises "Insufficient funds" which ``change_status``
    SWALLOWS, the order still reaches the terminal state, and NO money is minted
    (0 ORDER_PAYIN release legs).
  * A DIFFERENT ValidationException out of the release path is NOT swallowed
    (only "Insufficient funds" is) — it propagates.
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
from app.core.exceptions import ConflictException, ValidationException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.orders.service import OrderService
from app.modules.users.models import User

# Canonical money shape (mirrors the settlement / dispute templates).
AMOUNT = Decimal("100.0000")        # trader collateral / amount_usdt
FEE = Decimal("5.0000")             # merchant commission (NEVER taken on release)
TRADER_FEE = Decimal("2.0000")     # trader reward (NEVER paid on release)


@pytest.fixture(autouse=True)
def _stub_celery():
    """fail_order / cancel_order / change_status fire the merchant webhook +
    selector feedback + cascade-cancel through Celery — stub the broker so the
    test never reaches a real one. ``create=True`` so the patch holds even if the
    attr isn't bound."""
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


async def _setup_frozen(session, *, order_status):
    """Trader + merchant + a PAYIN order with the collateral ALREADY frozen in
    trader ESCROW (the PENDING / RECEIPT_UPLOADED pre-terminal state).

    The freeze is the REAL finance leg (trader WORK → ESCROW), so the ORDER_PAYIN
    freeze entry exists in the ledger and a correctly-released order ends with
    EXACTLY 2 ORDER_PAYIN legs (freeze + release)."""
    trader = await _mk_user(session, username=f"trader_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])

    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.WORK)
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=order_status)

    from app.modules.finance.service import FinanceService
    await FinanceService(session).create_order(order=order, trader=trader)
    # Post-freeze: collateral sits in trader ESCROW, WORK drained to 0.
    return trader, merchant, order


def _total(*amounts) -> Decimal:
    return sum(amounts, Decimal("0"))


async def _all_settlement_legs_zero(session, order_id):
    """No settlement legs exist for a released order."""
    assert len(await _legs(session, order_id, LedgerReferenceType.SYSTEM_COMMISSION)) == 0
    assert len(await _legs(session, order_id, LedgerReferenceType.TRADER_REWARD)) == 0
    assert len(await _legs(session, order_id, LedgerReferenceType.TEAMLEAD_REWARD)) == 0


# ── CORRECT BEHAVIOUR: a single FAILED release ──────────────────────────────


@pytest.mark.asyncio
async def test_fail_order_releases_collateral_two_payin_legs_no_settlement(session):
    """A correctly FAILED payin order releases the frozen collateral back to the
    trader's WORK: ESCROW drained to 0, WORK back to the full 100. The ledger has
    EXACTLY 2 ORDER_PAYIN legs (freeze + release) and ZERO settlement legs
    (commission / trader reward / teamlead reward). Merchant + system untouched."""
    trader, merchant, order = await _setup_frozen(session, order_status=OrderStatus.PENDING)
    # Sanity: collateral frozen pre-release.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT
    assert await _bal(session, user_id=trader.id) == Decimal("0")

    out = await OrderService(session).fail_order(order.id, "payment timed out")

    assert out.status == OrderStatus.FAILED
    # Collateral fully released, nothing settled.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, user_id=trader.id) == AMOUNT                  # WORK back to full
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")     # merchant untouched
    assert await _bal(session, is_system=True) == Decimal("0")             # system untouched

    # Ledger shape: freeze + release ⇒ exactly 2 ORDER_PAYIN legs, no settlement.
    payin = await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)
    assert len(payin) == 2
    await _all_settlement_legs_zero(session, order.id)
    # The two legs are opposite directions: freeze (WORK→ESCROW) then release
    # (ESCROW→WORK). Distinct from/to pairs, same amount.
    pairs = {(e.from_balance_id, e.to_balance_id) for e in payin}
    assert len(pairs) == 2
    assert all(e.amount == AMOUNT for e in payin)


@pytest.mark.asyncio
async def test_fail_order_from_receipt_uploaded_also_releases(session):
    """RECEIPT_UPLOADED is FAILABLE too (e.g. moderation rejected the receipt) —
    failing from it releases identically (2 ORDER_PAYIN legs, no settlement)."""
    trader, merchant, order = await _setup_frozen(session, order_status=OrderStatus.RECEIPT_UPLOADED)

    out = await OrderService(session).fail_order(order.id, "receipt rejected")

    assert out.status == OrderStatus.FAILED
    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)) == 2
    await _all_settlement_legs_zero(session, order.id)


# ── CORRECT BEHAVIOUR: a single CANCELED release (merchant-facing) ───────────


@pytest.mark.asyncio
async def test_cancel_order_releases_collateral_two_payin_legs_no_settlement(session):
    """The merchant-facing ``cancel_order`` (PENDING → CANCELED) releases the
    collateral exactly like a fail: 2 ORDER_PAYIN legs, no settlement, trader
    WORK restored, merchant + system untouched."""
    trader, merchant, order = await _setup_frozen(session, order_status=OrderStatus.PENDING)

    out = await OrderService(session).cancel_order(
        merchant, order_id=str(order.uuid), reason="Cancelled by merchant",
    )

    assert out.status == OrderStatus.CANCELED
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")

    assert len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)) == 2
    await _all_settlement_legs_zero(session, order.id)


# ── CORRECT BEHAVIOUR: canceling a CREATED (never-frozen) order ──────────────


@pytest.mark.asyncio
async def test_cancel_created_order_releases_nothing_no_legs(session):
    """A CREATED order never had its collateral frozen (no ESCROW). Canceling it
    (CREATED → CANCELED) attempts an ESCROW(0)→WORK release that raises
    "Insufficient funds"; ``change_status`` SWALLOWS that, so the order still
    reaches CANCELED but NO money moves and NO ORDER_PAYIN release leg is written.
    No money is minted."""
    trader = await _mk_user(session, username=f"trader_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    # Trader holds WORK but the order is NOT frozen (CREATED, never assigned).
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.WORK)
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.CREATED)

    out = await OrderService(session).cancel_order(
        merchant, order_id=str(order.uuid), reason="never paid",
    )

    assert out.status == OrderStatus.CANCELED
    # WORK untouched (the swallowed release moved nothing), ESCROW still 0.
    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")
    # NO ledger legs at all: no freeze (never frozen) and no release (swallowed).
    assert len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)) == 0
    await _all_settlement_legs_zero(session, order.id)


@pytest.mark.asyncio
async def test_fail_created_order_releases_nothing_no_legs(session):
    """Same as cancel: failing a CREATED (never-frozen) order reaches FAILED with
    the ESCROW(0)→WORK release swallowed → zero legs, zero money moved."""
    trader = await _mk_user(session, username=f"trader_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.WORK)
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=OrderStatus.CREATED)

    out = await OrderService(session).fail_order(order.id, "expired before assignment")

    assert out.status == OrderStatus.FAILED
    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)) == 0
    await _all_settlement_legs_zero(session, order.id)


# ── ADVERSARIAL 1: double-fail → release runs ONCE ──────────────────────────


@pytest.mark.asyncio
async def test_double_fail_releases_money_exactly_once(session):
    """Calling ``fail_order`` TWICE on the same order releases the collateral only
    ONCE. The first fail moves PENDING → FAILED; the SECOND is rejected at
    ``fail_order``'s OWN guard (FAILED is not in FAILABLE_STATUSES) with
    ConflictException — it never reaches the finance release. So the trader's WORK
    is NOT doubled and the ORDER_PAYIN leg count stays 2 (freeze + one release).

    NOTE: protection here sits in ``fail_order``'s ``FAILABLE_STATUSES`` check, a
    layer ABOVE the same-status no-op in ``change_status`` — so a double-fail is a
    hard ConflictException, not a silent idempotent return."""
    trader, merchant, order = await _setup_frozen(session, order_status=OrderStatus.PENDING)
    svc = OrderService(session)

    first = await svc.fail_order(order.id, "timeout")
    assert first.status == OrderStatus.FAILED
    bals_after_first = (
        await _bal(session, user_id=trader.id),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, is_system=True),
    )
    legs_after_first = len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN))

    # SECOND fail (Celery retry / cabinet+task race) — rejected, no second release.
    with pytest.raises(ConflictException, match="cannot be failed"):
        await svc.fail_order(order.id, "timeout again")

    bals_after_second = (
        await _bal(session, user_id=trader.id),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, is_system=True),
    )
    legs_after_second = len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN))

    # Money moved EXACTLY once: full collateral in WORK, ESCROW 0, no minting.
    assert bals_after_second == bals_after_first == (AMOUNT, Decimal("0"), Decimal("0"), Decimal("0"))
    # Leg count unchanged by the rejected second call (freeze + ONE release).
    assert legs_after_second == legs_after_first == 2
    await _all_settlement_legs_zero(session, order.id)


# ── ADVERSARIAL 2: double-cancel → release runs ONCE ────────────────────────


@pytest.mark.asyncio
async def test_double_cancel_releases_money_exactly_once(session):
    """Calling the merchant-facing ``cancel_order`` TWICE releases the collateral
    only ONCE. The second cancel is rejected at ``cancel_order``'s OWN guard
    (CANCELED is not in CANCELLABLE_STATUSES) with ConflictException before any
    finance call — so WORK is not doubled and the leg count stays 2."""
    trader, merchant, order = await _setup_frozen(session, order_status=OrderStatus.PENDING)
    svc = OrderService(session)

    first = await svc.cancel_order(merchant, order_id=str(order.uuid), reason="cancel")
    assert first.status == OrderStatus.CANCELED
    bals_after_first = (
        await _bal(session, user_id=trader.id),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, is_system=True),
    )
    legs_after_first = len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN))

    with pytest.raises(ConflictException, match="cannot be cancelled"):
        await svc.cancel_order(merchant, order_id=str(order.uuid), reason="cancel again")

    bals_after_second = (
        await _bal(session, user_id=trader.id),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, is_system=True),
    )
    legs_after_second = len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN))

    assert bals_after_second == bals_after_first == (AMOUNT, Decimal("0"), Decimal("0"), Decimal("0"))
    assert legs_after_second == legs_after_first == 2
    await _all_settlement_legs_zero(session, order.id)


@pytest.mark.asyncio
async def test_fail_after_cancel_is_illegal_transition_no_double_release(session):
    """A CANCELED order is terminal; ``fail_order`` on it is rejected by its own
    FAILABLE_STATUSES guard (CANCELED is not failable) with ConflictException —
    so a cancel-then-fail race can NEVER release the collateral twice. Books stay
    exactly at the single-release state."""
    trader, merchant, order = await _setup_frozen(session, order_status=OrderStatus.PENDING)
    svc = OrderService(session)

    await svc.cancel_order(merchant, order_id=str(order.uuid), reason="cancel")
    assert await _bal(session, user_id=trader.id) == AMOUNT

    with pytest.raises(ConflictException):
        await svc.fail_order(order.id, "late fail")

    # Single release only: WORK still 100, ESCROW 0, leg count still 2.
    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)) == 2


# ── ADVERSARIAL 3: insufficient-funds SWALLOW vs. other ValidationException ──


@pytest.mark.asyncio
async def test_drained_escrow_release_swallowed_order_still_failed_no_minting(session):
    """If the trader's ESCROW has been drained out from under a frozen order
    (e.g. an out-of-band reconciliation), the FAILED release's ESCROW→WORK
    transfer raises "Insufficient funds". ``change_status`` SWALLOWS exactly that
    error: the order STILL reaches FAILED, and NO money is minted into WORK (the
    release simply didn't happen — there was nothing to release)."""
    trader, merchant, order = await _setup_frozen(session, order_status=OrderStatus.PENDING)
    # Drain the trader's ESCROW to 0 behind the order's back.
    escrow = (
        await session.execute(
            select(Balance).where(
                Balance.user_id == trader.id,
                Balance.type == BalanceType.ESCROW,
                Balance.currency == Currency.USDT,
            )
        )
    ).scalars().first()
    escrow.amount = Decimal("0")
    await session.flush()

    out = await OrderService(session).fail_order(order.id, "timeout, escrow already gone")

    # Order reached FAILED despite the swallowed release.
    assert out.status == OrderStatus.FAILED
    # NO money minted: ESCROW stays 0, WORK stays 0 (the freeze drained it and the
    # release was swallowed — nothing came back, but nothing was created either).
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    assert await _bal(session, user_id=trader.id) == Decimal("0")
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")
    # Only the freeze leg exists; the swallowed release wrote NO ORDER_PAYIN entry.
    assert len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)) == 1
    await _all_settlement_legs_zero(session, order.id)


@pytest.mark.asyncio
async def test_non_insufficient_validation_error_is_not_swallowed(session):
    """The release branch swallows ONLY "Insufficient funds" — the guard is
    narrow (``if "Insufficient funds" not in str(e): raise``). A DIFFERENT
    ValidationException out of ``FinanceService.cancel_order`` must PROPAGATE.

    We patch ``FinanceService.cancel_order`` to raise a generic
    ``ValidationException`` (no "Insufficient funds" substring) on the FAILED
    release and assert ``fail_order`` re-raises it rather than reaching FAILED."""
    trader, merchant, order = await _setup_frozen(session, order_status=OrderStatus.PENDING)

    from app.modules.finance.service import FinanceService

    async def _boom(*args, **kwargs):
        raise ValidationException("Currency mismatch on from_balance")

    with patch.object(FinanceService, "cancel_order", _boom):
        with pytest.raises(ValidationException) as exc:
            await OrderService(session).fail_order(order.id, "timeout")

    assert "Insufficient funds" not in str(exc.value)
    assert "Currency mismatch" in str(exc.value)


# ── value conservation across a double release ──────────────────────────────


@pytest.mark.asyncio
async def test_total_value_conserved_under_double_fail(session):
    """Across a double ``fail_order``, the grand total of every bucket stays
    EXACTLY the original 100 collateral — no money created (a double-release bug)
    or destroyed. The 100 simply returns from ESCROW to the trader's WORK once."""
    trader, merchant, order = await _setup_frozen(session, order_status=OrderStatus.PENDING)
    svc = OrderService(session)

    before = _total(
        await _bal(session, user_id=trader.id),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, is_system=True),
    )
    assert before == AMOUNT

    await svc.fail_order(order.id, "timeout")
    # The second fail is rejected (FAILED not failable) — caught so the assertion
    # below proves the rejected retry minted/destroyed nothing.
    with pytest.raises(ConflictException):
        await svc.fail_order(order.id, "timeout retry")  # double

    after = _total(
        await _bal(session, user_id=trader.id),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, is_system=True),
    )
    assert after == before == AMOUNT
    # And it all sits back in the trader's WORK.
    assert await _bal(session, user_id=trader.id) == AMOUNT
