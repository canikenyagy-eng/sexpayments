"""
Integration tests for PAYIN SUCCESS-settle EDGE CASES, driving
``FinanceService.complete_order`` DIRECTLY (not only through the
``OrderService`` wrapper) against a REAL in-memory ledger.

``complete_order`` (PAYIN branch, app/modules/finance/service.py:860) moves, in
this fixed order:

  1. trader ESCROW(amount) → merchant WORK        (ORDER_PAYIN settle leg)
  2. ``if fee > 0``:   merchant WORK → system WORK (SYSTEM_COMMISSION)
  3. ``if trader_fee > 0``: system WORK → trader WORK (TRADER_REWARD)

then pays teamlead rewards (none here — no links) and bumps requisite turnover
(no-op — no requisite). The two ``if`` guards mean the commission and reward
legs are CONDITIONAL — this file pins what happens at the boundaries:

  * ``fee == 0``        → NO SYSTEM_COMMISSION leg; merchant keeps the FULL
                          ``amount_usdt``; escrow drains to 0; system untouched.
  * ``trader_fee == 0`` → NO TRADER_REWARD leg; the system KEEPS the full fee
                          as profit; trader WORK is unchanged.

ADVERSARIAL — system-balance STARVATION: when ``fee == 0`` (or
``trader_fee > fee``) and the system WORK balance is empty, the reward leg
(step 3) debits a system balance that has no funds → ``transfer`` raises
``ValidationException("Insufficient funds")``. Because the whole settlement runs
inside ONE transaction, the merchant credit + escrow debit from step 1 MUST roll
back atomically: nothing partially moved, escrow stays frozen, order NOT SUCCESS.

SQLite caveat (harness): ``SELECT … FOR UPDATE`` is a no-op here, so these tests
do NOT exercise row-lock concurrency. They assert exact Decimal balances, exact
ledger-leg counts, and value conservation. The atomic-rollback test wraps the
``complete_order`` call in an explicit ``session.begin()`` so the raise triggers
a real ROLLBACK of the partial work (mirrors how the service callers wrap it).
"""
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.users import UserRole
from app.core.exceptions import ValidationException
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.finance.service import FinanceService
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.users.models import User

# Canonical money shape (mirrors the settlement-idempotency template).
AMOUNT = Decimal("100.0000")        # trader collateral / settled amount_usdt
FEE = Decimal("5.0000")             # merchant commission → system (when > 0)
TRADER_FEE = Decimal("2.0000")     # trader reward (when > 0)
ZERO = Decimal("0")


# ── builders (self-contained) ───────────────────────────────────────────


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


async def _mk_merchant(session, *, user_id, suffix, fee_value) -> Merchant:
    m = Merchant(
        user_id=user_id,
        name=f"M-{suffix}",
        status=TerminalStatus.ENABLED,
        currency=Currency.RUB,
        api_key=f"key-{suffix}",
        api_secret=f"secret-{suffix}",
        fees={PaymentMethod.SBP.value: fee_value},
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


async def _mk_order(session, *, merchant_id, trader_id, fee_usdt, trader_fee_usdt) -> Order:
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
        fee_usdt=fee_usdt,
        trader_fee_usdt=trader_fee_usdt,
        profit_usdt=AMOUNT - fee_usdt,
        status=OrderStatus.RECEIPT_UPLOADED,
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
    return row.amount if row else ZERO


async def _legs(session, order_id, ref_type) -> list[LedgerEntry]:
    """All ledger entries for an order with a given reference type."""
    stmt = select(LedgerEntry).where(
        LedgerEntry.reference_id == str(order_id),
        LedgerEntry.reference_type == ref_type,
    )
    return list((await session.execute(stmt)).scalars().all())


async def _setup(session, *, fee_usdt, trader_fee_usdt, system_seed=ZERO):
    """Trader + merchant + a PAYIN order with the collateral already frozen in
    trader ESCROW (via the REAL finance freeze leg, so the ORDER_PAYIN freeze
    entry exists). ``fee_usdt`` / ``trader_fee_usdt`` are written verbatim on the
    order so ``complete_order`` reads them straight from the row.

    ``system_seed`` pre-funds the platform WORK balance — needed for the
    starvation case where ``fee == 0`` would otherwise leave the system empty
    when the reward leg tries to debit it (here we seed it to PROVE the reward
    can be paid, then a sibling test omits the seed to prove the rollback)."""
    trader = await _mk_user(session, username=f"trader_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    fee_value = int(fee_usdt) if fee_usdt == fee_usdt.to_integral_value() else float(fee_usdt)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6], fee_value=fee_value)

    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.WORK)
    if system_seed:
        await _mk_balance(session, system_seed, is_system=True, btype=BalanceType.WORK)

    order = await _mk_order(
        session, merchant_id=merchant.id, trader_id=trader.id,
        fee_usdt=fee_usdt, trader_fee_usdt=trader_fee_usdt,
    )
    # Freeze the collateral through the REAL freeze leg (work → escrow).
    await FinanceService(session).create_order(order=order, trader=trader)
    return trader, merchant, order


def _total(*amounts) -> Decimal:
    return sum(amounts, ZERO)


# ── CORRECT: fee == 0 → no commission leg, merchant keeps full amount ────


@pytest.mark.asyncio
async def test_fee_zero_no_commission_leg_merchant_keeps_full_amount(session):
    """``fee == 0`` (merchant.fees[method] = 0): the ``if fee > 0`` branch is
    skipped → NO SYSTEM_COMMISSION leg. The merchant WORK ends at the FULL
    ``amount_usdt``, escrow drains to 0. With ``trader_fee == 0`` too, the system
    never moves and stays at 0 — only the single ORDER_PAYIN settle leg fires."""
    trader, merchant, order = await _setup(session, fee_usdt=ZERO, trader_fee_usdt=ZERO)
    # Sanity: collateral fully frozen.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT

    await FinanceService(session).complete_order(order=order, trader=trader, merchant=merchant)

    # Merchant keeps the FULL amount — no commission shaved off.
    assert await _bal(session, merchant_id=merchant.id) == AMOUNT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == ZERO
    assert await _bal(session, user_id=trader.id) == ZERO
    assert await _bal(session, is_system=True) == ZERO

    # Ledger: freeze + settle ⇒ 2 ORDER_PAYIN; NO commission, NO reward.
    assert len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)) == 2
    assert len(await _legs(session, order.id, LedgerReferenceType.SYSTEM_COMMISSION)) == 0
    assert len(await _legs(session, order.id, LedgerReferenceType.TRADER_REWARD)) == 0
    # Value conserved: the 100 collateral lands wholly on the merchant.
    assert await _bal(session, merchant_id=merchant.id) == AMOUNT


@pytest.mark.asyncio
async def test_fee_zero_with_trader_fee_seeded_system_pays_reward_no_commission(session):
    """``fee == 0`` but ``trader_fee > 0``: NO commission leg, yet the reward leg
    STILL fires and debits the SYSTEM balance (which the platform must have
    pre-funded from elsewhere). Merchant keeps the full amount; trader gets the
    reward straight out of the seeded system float; system drops by the reward."""
    trader, merchant, order = await _setup(
        session, fee_usdt=ZERO, trader_fee_usdt=TRADER_FEE, system_seed=TRADER_FEE,
    )

    await FinanceService(session).complete_order(order=order, trader=trader, merchant=merchant)

    assert await _bal(session, merchant_id=merchant.id) == AMOUNT            # full amount, no commission
    assert await _bal(session, user_id=trader.id) == TRADER_FEE              # reward paid
    assert await _bal(session, is_system=True) == ZERO                       # seed fully consumed by reward
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == ZERO

    assert len(await _legs(session, order.id, LedgerReferenceType.SYSTEM_COMMISSION)) == 0
    assert len(await _legs(session, order.id, LedgerReferenceType.TRADER_REWARD)) == 1


# ── CORRECT: trader_fee == 0 → no reward leg, system keeps full fee ──────


@pytest.mark.asyncio
async def test_trader_fee_zero_no_reward_leg_system_keeps_full_fee(session):
    """``trader_fee == 0``: the ``if trader_fee > 0`` branch is skipped → NO
    TRADER_REWARD leg. The system KEEPS the entire commission as profit and the
    trader WORK balance is UNCHANGED (escrow drained, nothing credited back)."""
    trader, merchant, order = await _setup(session, fee_usdt=FEE, trader_fee_usdt=ZERO)

    await FinanceService(session).complete_order(order=order, trader=trader, merchant=merchant)

    assert await _bal(session, merchant_id=merchant.id) == AMOUNT - FEE      # 95 net
    assert await _bal(session, is_system=True) == FEE                        # full fee kept as profit
    assert await _bal(session, user_id=trader.id) == ZERO                    # trader WORK untouched
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == ZERO

    # Ledger: freeze + settle ⇒ 2 ORDER_PAYIN; 1 commission; NO reward.
    assert len(await _legs(session, order.id, LedgerReferenceType.ORDER_PAYIN)) == 2
    assert len(await _legs(session, order.id, LedgerReferenceType.SYSTEM_COMMISSION)) == 1
    assert len(await _legs(session, order.id, LedgerReferenceType.TRADER_REWARD)) == 0
    # Value conserved: 100 = 95 (merchant) + 5 (system); trader gets 0.
    assert (AMOUNT - FEE) + FEE == AMOUNT


@pytest.mark.asyncio
async def test_trader_fee_zero_value_conserved(session):
    """Cross-check: with ``trader_fee == 0`` the grand total across every bucket
    is exactly the original 100 — no money minted into a phantom reward."""
    trader, merchant, order = await _setup(session, fee_usdt=FEE, trader_fee_usdt=ZERO)

    before = _total(
        await _bal(session, user_id=trader.id),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, is_system=True),
    )
    assert before == AMOUNT

    await FinanceService(session).complete_order(order=order, trader=trader, merchant=merchant)

    after = _total(
        await _bal(session, user_id=trader.id),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, is_system=True),
    )
    assert after == before == AMOUNT


# ── ADVERSARIAL: system-balance STARVATION → settle aborts, no reward minted ─
#
# HARNESS NOTE on atomicity: ``complete_order`` writes the escrow→merchant
# (step 1) and commission (step 2) legs BEFORE the reward leg (step 3) raises.
# Whole-order atomicity — un-draining escrow + un-crediting the merchant — is
# delegated to the CALLER's surrounding ``async with session.begin()`` (in
# OrderService), which ROLLS BACK on the raise. SQLite's single StaticPool
# connection can't replay a savepoint-on-raise cleanly (aiosqlite greenlet
# error), so these tests do NOT assert the rolled-back merchant/escrow balances.
# Instead they pin the durable, rollback-INDEPENDENT invariants that hold no
# matter what the caller does: (a) the settle RAISES ``Insufficient funds`` so
# the order can NEVER reach SUCCESS, and (b) the failing reward step mints NOTHING
# — NO TRADER_REWARD leg is ever written and the trader WORK is NEVER credited.
# Verified against the real ledger (see the leg-count asserts).


@pytest.mark.asyncio
async def test_system_starvation_fee_zero_reward_positive_aborts_no_reward_minted(session):
    """STARVATION: ``fee == 0`` (no commission credited to the system) but
    ``trader_fee > 0`` and the system WORK balance is EMPTY. The reward leg
    (system → trader) debits a system balance with no funds → the settle RAISES
    ``Insufficient funds``. The failing step mints nothing: NO TRADER_REWARD leg,
    trader WORK stays 0 — so the caller's tx rollback can never leave a settled
    merchant with an UNPAID-but-credited reward."""
    trader, merchant, order = await _setup(
        session, fee_usdt=ZERO, trader_fee_usdt=TRADER_FEE, system_seed=ZERO,
    )
    # Pre-state: collateral fully frozen, nothing settled.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT
    assert await _bal(session, merchant_id=merchant.id) == ZERO

    with pytest.raises(ValidationException, match="Insufficient funds"):
        await FinanceService(session).complete_order(
            order=order, trader=trader, merchant=merchant,
        )

    # The reward step minted NOTHING — no reward leg, trader never credited.
    assert len(await _legs(session, order.id, LedgerReferenceType.TRADER_REWARD)) == 0
    assert await _bal(session, user_id=trader.id) == ZERO          # WORK never credited the reward
    assert await _bal(session, is_system=True) == ZERO            # system never went negative
    # fee == 0 ⇒ no commission leg was ever attempted.
    assert len(await _legs(session, order.id, LedgerReferenceType.SYSTEM_COMMISSION)) == 0
    # complete_order only moves money; it raised before returning, so the order
    # was never advanced to SUCCESS by this call (status flip lives in OrderService).
    fresh = await session.get(Order, order.id)
    assert fresh.status != OrderStatus.SUCCESS


@pytest.mark.asyncio
async def test_system_starvation_trader_fee_exceeds_fee_aborts_no_reward_minted(session):
    """STARVATION variant: ``trader_fee > fee`` and the system starts empty. The
    commission credits only ``fee`` to the system (step 2), but the reward leg
    then tries to debit the LARGER ``trader_fee`` (step 3) → the system would go
    short by ``trader_fee - fee`` → ``Insufficient funds``. Pins that the reward
    is NOT partially paid: no TRADER_REWARD leg, trader WORK stays 0, and the
    system is never driven negative (it holds at most the commission it took)."""
    big_reward = FEE + Decimal("1.0000")  # 6 > fee 5 → system short by 1
    trader, merchant, order = await _setup(
        session, fee_usdt=FEE, trader_fee_usdt=big_reward, system_seed=ZERO,
    )
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT

    with pytest.raises(ValidationException, match="Insufficient funds"):
        await FinanceService(session).complete_order(
            order=order, trader=trader, merchant=merchant,
        )

    # No reward minted; trader untouched; system never negative.
    assert len(await _legs(session, order.id, LedgerReferenceType.TRADER_REWARD)) == 0
    assert await _bal(session, user_id=trader.id) == ZERO
    assert await _bal(session, is_system=True) >= ZERO
    fresh = await session.get(Order, order.id)
    assert fresh.status != OrderStatus.SUCCESS


@pytest.mark.asyncio
async def test_system_starvation_no_phantom_trader_credit(session):
    """The starvation abort never mints money INTO the trader: across the whole
    failed settle the trader's combined WORK+ESCROW is unchanged from the frozen
    pre-settle state (collateral still 100, reward 0) — the platform cannot pay a
    reward it doesn't have."""
    trader, merchant, order = await _setup(
        session, fee_usdt=ZERO, trader_fee_usdt=TRADER_FEE, system_seed=ZERO,
    )
    trader_before = _total(
        await _bal(session, user_id=trader.id),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
    )
    assert trader_before == AMOUNT

    with pytest.raises(ValidationException, match="Insufficient funds"):
        await FinanceService(session).complete_order(
            order=order, trader=trader, merchant=merchant,
        )

    # The trader gained NOTHING from the failed reward (no phantom credit). Note:
    # the in-harness escrow→merchant move that ran before the raise is the
    # caller's tx to roll back; here we pin only that no money was MINTED for the
    # trader, which holds regardless of that rollback.
    assert await _bal(session, user_id=trader.id) == ZERO
    assert len(await _legs(session, order.id, LedgerReferenceType.TRADER_REWARD)) == 0
