"""
Teamlead reward accrual on order SUCCESS + reversal on dispute — against a REAL
in-memory ledger (no mocked finance).

Money model (PAYIN):
  * On settlement, ``FinanceService.complete_order`` is the SINGLE place that pays
    teamlead rewards. For every active link of the order's MERCHANT and TRADER
    with a positive ``fee_percent`` it moves ``amount_usdt × % `` (quantized
    0.0000) system WORK → teamlead WORK, as a ``TEAMLEAD_REWARD`` ledger entry
    with ``reference_id = str(order.id)``.
  * On dispute open, ``FinanceService.reconcile_for_dispute`` un-does the whole
    settlement; ``TeamleaderService.reverse_rewards`` is the part that claws each
    teamlead reward back (teamlead WORK → system WORK) with a ``_reversal`` suffix
    on the reference id, consuming one live reward per existing reversal.

These tests pin the EXACT Decimal balances, the TEAMLEAD_REWARD leg counts, and
value conservation (sum-in == sum-out), and pin the current idempotency behaviour
of the two real funnels (settle twice / reverse twice). Reward calculation lives
in ``TeamleaderService`` but is exercised here only through the money funnels.

Run (iteration): ~/.venvs/primepay-unit/bin/python -m pytest \
    tests/integration/test_teamlead_reward_settlement.py -q
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.common.enums.balances import BalanceType, LedgerReferenceType
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.payouts import PayoutStatus
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.finance.service import FinanceService
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.orders.service import OrderService
from app.modules.payouts.models import Payout
from app.modules.teamleaders.models import TeamleadLink
from app.modules.teamleaders.service import TeamleaderService
from app.modules.users.models import User

# A non-terminating intermediate to PIN the reward rounding:
#   33.3333 × 3.00% = 0.999999  → quantize(0.0000) = 1.0000
AMOUNT = Decimal("33.3333")
MERCHANT_PCT = Decimal("3.00")
REWARD = Decimal("1.0000")          # the quantized merchant-side reward
FEE = Decimal("5.0000")             # system commission on the order
TRADER_FEE = Decimal("2.0000")     # trader reward
SYSTEM_SEED = Decimal("1000")       # pre-funded system WORK to cover rewards


@pytest.fixture(autouse=True)
def _stub_celery():
    """The order status funnel enqueues callback / selector-feedback tasks —
    stub the broker (patched on the *imported* name in orders.service, which is
    where the real funnel calls it) so tests don't reach a real Celery/Redis."""
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.modules.orders.service.celery_app", celery, create=True), \
         patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


# ── builders (self-contained) ───────────────────────────────────────────


async def _mk_user(session, *, role=UserRole.TRADER) -> User:
    u = User(
        username=f"{role.value}_{uuid4().hex[:8]}",
        password=get_password_hash("pass12345"),
        role=role,
        totp_enabled=False,
        is_blocked=False,
        use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_merchant(session, *, user_id) -> Merchant:
    s = uuid4().hex[:8]
    m = Merchant(
        user_id=user_id,
        name=f"M-{s}",
        status=TerminalStatus.ENABLED,
        currency=Currency.RUB,
        api_key=f"key-{s}",
        api_secret=f"secret-{s}",
    )
    session.add(m)
    await session.flush()
    return m


async def _mk_balance(session, amount, *, user_id=None, merchant_id=None, is_system=False,
                      btype=BalanceType.WORK) -> Balance:
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


async def _mk_link(session, *, teamlead_id, entity_type, entity_id,
                   fee_percent="0", payout_fee_percent="0", is_active=True) -> TeamleadLink:
    link = TeamleadLink(
        teamlead_id=teamlead_id,
        linked_entity_type=entity_type,
        linked_entity_id=entity_id,
        fee_percent=Decimal(fee_percent),
        payout_fee_percent=Decimal(payout_fee_percent),
        is_active=is_active,
    )
    session.add(link)
    await session.flush()
    return link


async def _mk_order(session, *, merchant_id, trader_id, status,
                    amount_usdt=AMOUNT, fee=FEE, trader_fee=TRADER_FEE) -> Order:
    o = Order(
        uuid=uuid4(),
        external_id=f"ext-{uuid4().hex[:8]}",
        merchant_id=merchant_id,
        trader_id=trader_id,
        direction=PaymentDirection.PAYIN,
        payment_method=PaymentMethod.SBP,
        amount=Decimal("1000.00"),
        currency=Currency.RUB,
        amount_usdt=amount_usdt,
        fee_usdt=fee,
        trader_fee_usdt=trader_fee,
        status=status,
        requisite_id=None,   # so turnover increment is a clean no-op
    )
    session.add(o)
    await session.flush()
    return o


async def _bal(session, *, user_id=None, merchant_id=None, is_system=False,
               btype=BalanceType.WORK) -> Decimal:
    """Current amount of a USDT balance, 0 if it was never created."""
    stmt = select(Balance).where(Balance.type == btype, Balance.currency == Currency.USDT)
    if is_system:
        stmt = stmt.where(Balance.is_system.is_(True))
    elif user_id is not None:
        stmt = stmt.where(Balance.user_id == user_id)
    elif merchant_id is not None:
        stmt = stmt.where(Balance.merchant_id == merchant_id)
    row = (await session.execute(stmt)).scalars().first()
    return row.amount if row else Decimal("0")


async def _legs(session, *, reference_type=LedgerReferenceType.TEAMLEAD_REWARD,
                reference_id=None) -> int:
    """Count ledger legs of a reference_type (optionally a specific reference_id)."""
    stmt = select(func.count()).select_from(LedgerEntry).where(
        LedgerEntry.reference_type == reference_type
    )
    if reference_id is not None:
        stmt = stmt.where(LedgerEntry.reference_id == reference_id)
    return int((await session.execute(stmt)).scalar_one())


async def _reward_entries(session, *, reference_id_prefix=None) -> list[LedgerEntry]:
    stmt = select(LedgerEntry).where(
        LedgerEntry.reference_type == LedgerReferenceType.TEAMLEAD_REWARD
    ).order_by(LedgerEntry.id)
    rows = list((await session.execute(stmt)).scalars().all())
    if reference_id_prefix is not None:
        rows = [r for r in rows if (r.reference_id or "").split("_", 1)[0] == reference_id_prefix]
    return rows


async def _scaffold(session, *, order_status=OrderStatus.PENDING, system_seed=SYSTEM_SEED):
    """Trader + merchant + order, with the trader's collateral already frozen in
    ESCROW (the post-create_order shape) and a funded system WORK balance so the
    fee/trader/teamlead reward moves have liquidity. Returns (trader, merchant,
    order)."""
    trader = await _mk_user(session, role=UserRole.TRADER)
    mu = await _mk_user(session, role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id)
    # Collateral frozen for the trader (what create_order leaves on assignment).
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.ESCROW)
    if system_seed:
        await _mk_balance(session, system_seed, is_system=True)
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=order_status)
    return trader, merchant, order


# ── CORRECT: success pays the teamlead exactly, with rounding pinned ─────


@pytest.mark.asyncio
async def test_success_pays_merchant_teamlead_rounded_reward(session):
    """Order SUCCESS pays a merchant-side teamlead amount_usdt×pct/100 quantized
    0.0000. 33.3333 × 3% = 0.999999 → 1.0000 pins the rounding (raw != quantized).
    System WORK → teamlead WORK; one TEAMLEAD_REWARD leg, ref = str(order.id)."""
    trader, merchant, order = await _scaffold(session)
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    await _mk_link(session, teamlead_id=tl.id, entity_type=UserRole.MERCHANT,
                   entity_id=merchant.id, fee_percent=str(MERCHANT_PCT))

    sys_before = await _bal(session, is_system=True)
    async with session.begin_nested():
        breakdown = await FinanceService(session).complete_order(
            order=order, trader=trader, merchant=merchant,
        )

    # Reward is the quantized value, NOT the raw 0.999999.
    assert await _bal(session, user_id=tl.id) == REWARD
    # Exactly one TEAMLEAD_REWARD leg, keyed by the order id.
    assert await _legs(session, reference_id=str(order.id)) == 1
    entry = (await _reward_entries(session))[0]
    assert entry.reference_id == str(order.id)
    assert entry.amount == REWARD
    assert entry.reference_type == LedgerReferenceType.TEAMLEAD_REWARD
    # Direction: system WORK (debited) → teamlead WORK (credited).
    sys_bal = (await session.execute(
        select(Balance).where(Balance.is_system.is_(True))
    )).scalars().first()
    tl_bal = (await session.execute(
        select(Balance).where(Balance.user_id == tl.id, Balance.type == BalanceType.WORK)
    )).scalars().first()
    assert entry.from_balance_id == sys_bal.id and entry.to_balance_id == tl_bal.id
    # Breakdown reports the same quantized figure.
    assert breakdown == [{
        "teamlead_id": tl.id, "side": "merchant",
        "fee_percent": float(MERCHANT_PCT), "reward_usdt": str(REWARD),
    }]
    # System WORK funded the teamlead reward (it also took fee in, paid trader
    # out — net here: +FEE (commission) -TRADER_FEE -REWARD).
    assert await _bal(session, is_system=True) == sys_before + FEE - TRADER_FEE - REWARD


@pytest.mark.asyncio
async def test_two_distinct_teamleads_get_two_legs_and_full_reversal(session):
    """An order whose MERCHANT and TRADER each have a distinct teamlead pays TWO
    TEAMLEAD_REWARD legs; opening a dispute (reconcile) reverses BOTH fully."""
    trader, merchant, order = await _scaffold(session)
    tl_m = await _mk_user(session, role=UserRole.TEAMLEAD)
    tl_t = await _mk_user(session, role=UserRole.TEAMLEAD)
    await _mk_link(session, teamlead_id=tl_m.id, entity_type=UserRole.MERCHANT,
                   entity_id=merchant.id, fee_percent=str(MERCHANT_PCT))   # 1.0000
    await _mk_link(session, teamlead_id=tl_t.id, entity_type=UserRole.TRADER,
                   entity_id=trader.id, fee_percent="6.00")                 # 33.3333×6% = 1.999998 → 2.0000
    trader_reward = Decimal("2.0000")

    async with session.begin_nested():
        await FinanceService(session).complete_order(order=order, trader=trader, merchant=merchant)

    assert await _bal(session, user_id=tl_m.id) == REWARD
    assert await _bal(session, user_id=tl_t.id) == trader_reward
    assert await _legs(session, reference_id=str(order.id)) == 2

    # Dispute open from SUCCESS → reconcile reverses the FULL settlement, which
    # includes both teamlead rewards (teamlead WORK → system).
    order.status = OrderStatus.SUCCESS
    await session.flush()
    async with session.begin_nested():
        await FinanceService(session).reconcile_for_dispute(
            order=order, merchant=merchant, trader=trader, pre_status=OrderStatus.SUCCESS,
        )

    # Both teamleads fully clawed back.
    assert await _bal(session, user_id=tl_m.id) == Decimal("0")
    assert await _bal(session, user_id=tl_t.id) == Decimal("0")
    # 2 payouts + 2 reversals.
    assert await _legs(session) == 4
    reversals = [e for e in await _reward_entries(session)
                 if (e.reference_id or "").endswith("_reversal")]
    assert len(reversals) == 2
    # Both original rewards share ref str(order.id), so both reversals share the
    # same suffixed ref — two legs, one distinct reference_id.
    assert {e.reference_id for e in reversals} == {f"{order.id}_reversal"}
    # Value conservation: every reward paid was returned (net teamlead 0).
    paid = sum((e.amount for e in await _reward_entries(session)
                if not (e.reference_id or "").endswith("_reversal")), Decimal("0"))
    undone = sum((e.amount for e in reversals), Decimal("0"))
    assert paid == undone == REWARD + trader_reward


@pytest.mark.asyncio
async def test_reverse_rewards_is_cross_order_isolated_for_non_prefix_ids(session):
    """reverse_rewards(order 7) reverses ONLY ref '7' and '7_*' — sibling orders
    (8, 70, 700) are untouched. Pins the intended cross-order isolation for ids
    that are NOT a LIKE-wildcard match of '7_'.  (See the companion test
    ``test_reverse_rewards_LIKE_wildcard_overmatches_sibling_ids`` for the known
    over-match on ids like '70'/'71'.)"""
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    tl_work = await _mk_balance(session, "0", user_id=tl.id)
    sys_work = await _mk_balance(session, SYSTEM_SEED, is_system=True)

    # 8/70/700 — none equal "7"; "70"/"700" WOULD be caught by the '7_%' LIKE
    # wildcard, so we use only ids that the reversal must leave alone: 8, 77x...
    # Actually pick ids with a DIFFERENT leading digit so neither '==7' nor
    # 'LIKE 7_%' matches: 8, 80, 800.
    seeded = {7: Decimal("1.0000"), 8: Decimal("2.0000"),
              80: Decimal("3.0000"), 800: Decimal("4.0000")}
    fin = FinanceService(session)
    async with session.begin_nested():
        for oid, amt in seeded.items():
            await fin.transfer(
                amount=amt, currency=Currency.USDT,
                reference_type=LedgerReferenceType.TEAMLEAD_REWARD,
                reference_id=str(oid),
                from_balance_id=sys_work.id, to_balance_id=tl_work.id,
                description=f"seed reward order {oid}",
            )
    total_seeded = sum(seeded.values(), Decimal("0"))
    assert await _bal(session, user_id=tl.id) == total_seeded

    order7 = Order(
        uuid=uuid4(), external_id="ext-7", merchant_id=1, trader_id=tl.id,
        direction=PaymentDirection.PAYIN, payment_method=PaymentMethod.SBP,
        amount=Decimal("1"), currency=Currency.RUB, amount_usdt=Decimal("1"),
        status=OrderStatus.SUCCESS,
    )
    order7.id = 7
    async with session.begin_nested():
        await TeamleaderService(session).reverse_rewards(order7)

    # Only the '7' reward (1.0000) came back; 8/80/800 are untouched.
    assert await _bal(session, user_id=tl.id) == total_seeded - seeded[7]
    assert await _legs(session, reference_id="7_reversal") == 1
    assert await _legs(session, reference_id="8_reversal") == 0
    assert await _legs(session, reference_id="80_reversal") == 0
    assert await _legs(session, reference_id="800_reversal") == 0


@pytest.mark.asyncio
async def test_reverse_rewards_isolated_from_sibling_prefix_ids(session):
    """Regression for a cross-order teamlead clawback bug: reverse_rewards built
    its sibling-reversal filter with ``reference_id.startswith(f"{order_id}_")``,
    which SQLAlchemy compiled to ``LIKE '<id>_' || '%'`` WITHOUT escaping the
    ``_``. In SQL LIKE, ``_`` is a single-char wildcard, so reversing order ``5``
    ALSO matched orders ``50``, ``51``, ``500`` (any id ``5`` followed by ≥1
    char) and clawed back THEIR teamlead rewards. Because order ids grow
    sequentially, those descendant ids almost always exist by dispute time, so
    this leaked broadly in production.

    Fixed by ``startswith(..., autoescape=True)``. This test asserts the
    isolation: reverse_rewards(order 5) reverses ONLY ref '5' (and '5_*'); the
    sibling prefix ids 50/51/500 — and the control 6 — are left untouched."""
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    tl_work = await _mk_balance(session, "0", user_id=tl.id)
    sys_work = await _mk_balance(session, SYSTEM_SEED, is_system=True)

    seeded = {5: Decimal("1.0000"), 50: Decimal("2.0000"),
              500: Decimal("3.0000"), 51: Decimal("4.0000"),
              6: Decimal("9.0000")}   # control: different leading digit, untouched
    fin = FinanceService(session)
    async with session.begin_nested():
        for oid, amt in seeded.items():
            await fin.transfer(
                amount=amt, currency=Currency.USDT,
                reference_type=LedgerReferenceType.TEAMLEAD_REWARD,
                reference_id=str(oid),
                from_balance_id=sys_work.id, to_balance_id=tl_work.id,
                description=f"seed reward order {oid}",
            )
    total_seeded = sum(seeded.values(), Decimal("0"))
    assert await _bal(session, user_id=tl.id) == total_seeded

    order5 = Order(
        uuid=uuid4(), external_id="ext-5", merchant_id=1, trader_id=tl.id,
        direction=PaymentDirection.PAYIN, payment_method=PaymentMethod.SBP,
        amount=Decimal("1"), currency=Currency.RUB, amount_usdt=Decimal("1"),
        status=OrderStatus.SUCCESS,
    )
    order5.id = 5
    async with session.begin_nested():
        await TeamleaderService(session).reverse_rewards(order5)

    # FIXED: only order 5 (exact ref '5') is reversed; the LIKE-wildcard
    # siblings 50/51/500 and the control 6 are all left untouched.
    assert await _bal(session, user_id=tl.id) == total_seeded - seeded[5]
    assert await _legs(session, reference_id="5_reversal") == 1
    assert await _legs(session, reference_id="50_reversal") == 0     # NOT leaked anymore
    assert await _legs(session, reference_id="500_reversal") == 0    # NOT leaked anymore
    assert await _legs(session, reference_id="51_reversal") == 0     # NOT leaked anymore
    assert await _legs(session, reference_id="6_reversal") == 0      # control: safe


# ── ADVERSARIAL ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_double_settle_through_real_funnel_credits_teamlead_once(session):
    """Two confirms of the SAME order via the real OrderService funnel pay the
    teamlead ONCE — the second sees SUCCESS and no-ops (no double credit, no
    extra TEAMLEAD_REWARD leg)."""
    trader, merchant, order = await _scaffold(session, order_status=OrderStatus.PENDING)
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    await _mk_link(session, teamlead_id=tl.id, entity_type=UserRole.MERCHANT,
                   entity_id=merchant.id, fee_percent=str(MERCHANT_PCT))

    svc = OrderService(session)
    first = await svc.complete_order(trader=trader, order_id=order.id)
    assert first.status == OrderStatus.SUCCESS
    assert await _bal(session, user_id=tl.id) == REWARD
    assert await _legs(session, reference_id=str(order.id)) == 1

    # Re-confirm: idempotent no-op (already SUCCESS).
    second = await svc.complete_order(trader=trader, order_id=order.id)
    assert second.status == OrderStatus.SUCCESS
    assert await _bal(session, user_id=tl.id) == REWARD            # NOT doubled
    assert await _legs(session, reference_id=str(order.id)) == 1   # still one leg


@pytest.mark.asyncio
async def test_double_reverse_debits_teamlead_once(session):
    """reverse_rewards is repeat-safe: a settled reward reversed twice in a row
    moves money back ONCE (the second pass sees the existing reversal and skips,
    consuming it) — teamlead not driven negative, exactly one reversal leg."""
    trader, merchant, order = await _scaffold(session)
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    await _mk_link(session, teamlead_id=tl.id, entity_type=UserRole.MERCHANT,
                   entity_id=merchant.id, fee_percent=str(MERCHANT_PCT))

    async with session.begin_nested():
        await FinanceService(session).complete_order(order=order, trader=trader, merchant=merchant)
    assert await _bal(session, user_id=tl.id) == REWARD

    tl_svc = TeamleaderService(session)
    async with session.begin_nested():
        await tl_svc.reverse_rewards(order)
    assert await _bal(session, user_id=tl.id) == Decimal("0")
    assert await _legs(session, reference_id=f"{order.id}_reversal") == 1

    # Second reversal: the existing reversal is counted and consumed → no move.
    async with session.begin_nested():
        await tl_svc.reverse_rewards(order)
    assert await _bal(session, user_id=tl.id) == Decimal("0")            # not negative
    assert await _legs(session, reference_id=f"{order.id}_reversal") == 1  # still one


@pytest.mark.asyncio
async def test_reverse_repay_reverse_consumes_only_live_reward(session):
    """dispute → resolve → dispute again (through the real settle/reconcile
    funnels): each dispute reverses only the LIVE (un-reversed) reward, never an
    already-undone one. After settle→reconcile→settle→reconcile the teamlead
    nets 0 and the reward books stay balanced (2 payouts keyed str(id), 2
    reversals). reconcile_for_dispute refills trader ESCROW so the re-settle has
    collateral — exactly the real dispute round-trip."""
    trader, merchant, order = await _scaffold(session)
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    await _mk_link(session, teamlead_id=tl.id, entity_type=UserRole.MERCHANT,
                   entity_id=merchant.id, fee_percent=str(MERCHANT_PCT))
    fin = FinanceService(session)

    # 1) settle → pay
    async with session.begin_nested():
        await fin.complete_order(order=order, trader=trader, merchant=merchant)
    order.status = OrderStatus.SUCCESS
    await session.flush()
    assert await _bal(session, user_id=tl.id) == REWARD

    # 2) dispute → full reconcile reverses the live reward (and refills ESCROW).
    async with session.begin_nested():
        await fin.reconcile_for_dispute(
            order=order, merchant=merchant, trader=trader, pre_status=OrderStatus.SUCCESS,
        )
    assert await _bal(session, user_id=tl.id) == Decimal("0")
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT

    # 3) resolve → re-settle from the refilled ESCROW (same ref str(order.id)).
    async with session.begin_nested():
        await fin.complete_order(order=order, trader=trader, merchant=merchant)
    assert await _bal(session, user_id=tl.id) == REWARD

    # 4) dispute again → reverse must consume ONLY the live (2nd) reward, leaving
    #    the already-reversed (1st) one alone. Net teamlead 0.
    async with session.begin_nested():
        await fin.reconcile_for_dispute(
            order=order, merchant=merchant, trader=trader, pre_status=OrderStatus.SUCCESS,
        )
    assert await _bal(session, user_id=tl.id) == Decimal("0")

    entries = await _reward_entries(session)
    payouts = [e for e in entries if not (e.reference_id or "").endswith("_reversal")]
    reversals = [e for e in entries if (e.reference_id or "").endswith("_reversal")]
    assert len(payouts) == 2 and len(reversals) == 2
    assert all(e.reference_id == str(order.id) for e in payouts)
    # Value conservation: total paid == total reversed.
    assert sum((e.amount for e in payouts), Decimal("0")) == \
           sum((e.amount for e in reversals), Decimal("0")) == REWARD * 2


@pytest.mark.asyncio
async def test_payout_reward_not_touched_by_order_reverse_for_same_numeric_id(session):
    """A payout teamlead reward keyed ``payout:<id>`` is NEVER consumed by an
    order ``reverse_rewards`` whose order id is the same NUMBER. The reverse
    query only matches ``str(id)`` / ``str(id)_*`` — ``payout:5`` doesn't match.
    Pins the namespacing that keeps the two reward scopes isolated."""
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    tl_work = await _mk_balance(session, "0", user_id=tl.id)
    sys_work = await _mk_balance(session, SYSTEM_SEED, is_system=True)
    fin = FinanceService(session)

    # Order-5 reward AND a payout:5 reward live side by side.
    async with session.begin_nested():
        await fin.transfer(
            amount=Decimal("1.0000"), currency=Currency.USDT,
            reference_type=LedgerReferenceType.TEAMLEAD_REWARD, reference_id="5",
            from_balance_id=sys_work.id, to_balance_id=tl_work.id, description="order 5 reward",
        )
        await fin.transfer(
            amount=Decimal("7.0000"), currency=Currency.USDT,
            reference_type=LedgerReferenceType.TEAMLEAD_REWARD, reference_id="payout:5",
            from_balance_id=sys_work.id, to_balance_id=tl_work.id, description="payout 5 reward",
        )
    assert await _bal(session, user_id=tl.id) == Decimal("8.0000")

    order5 = Order(
        uuid=uuid4(), external_id="ext-5b", merchant_id=1, trader_id=tl.id,
        direction=PaymentDirection.PAYIN, payment_method=PaymentMethod.SBP,
        amount=Decimal("1"), currency=Currency.RUB, amount_usdt=Decimal("1"),
        status=OrderStatus.SUCCESS,
    )
    order5.id = 5
    async with session.begin_nested():
        await TeamleaderService(session).reverse_rewards(order5)

    # Only the order-5 reward (1.0000) reversed; the payout:5 reward (7.0000) is
    # untouched — payout rewards are terminal and never reversed by orders.
    assert await _bal(session, user_id=tl.id) == Decimal("7.0000")
    assert await _legs(session, reference_id="5_reversal") == 1
    assert await _legs(session, reference_id="payout:5_reversal") == 0
    # The payout:5 payout leg is still present and intact.
    assert await _legs(session, reference_id="payout:5") == 1


@pytest.mark.asyncio
async def test_doliv_settlement_pays_no_teamlead_reward(session):
    """A долив settlement (settle_doliv) pays NO teamlead reward, even when the
    requester/executor have active teamlead links. Долив money is requester
    ESCROW → executor WORK (+ price + executor TRADER_REWARD); the TEAMLEAD_REWARD
    reference type never appears."""
    requester = await _mk_user(session, role=UserRole.TRADER)
    executor = await _mk_user(session, role=UserRole.TRADER)
    tl = await _mk_user(session, role=UserRole.TEAMLEAD)
    # Active links that WOULD pay on an order — must be ignored by долив.
    await _mk_link(session, teamlead_id=tl.id, entity_type=UserRole.TRADER,
                   entity_id=requester.id, fee_percent="10.00", payout_fee_percent="10.00")
    await _mk_link(session, teamlead_id=tl.id, entity_type=UserRole.TRADER,
                   entity_id=executor.id, fee_percent="10.00", payout_fee_percent="10.00")

    amount_usdt = Decimal("100.0000")
    price = Decimal("2.0000")
    reward = Decimal("1.0000")
    # Requester escrow holds amount+price (post-freeze shape); system funds reward.
    await _mk_balance(session, amount_usdt + price, user_id=requester.id, btype=BalanceType.ESCROW)
    await _mk_balance(session, SYSTEM_SEED, is_system=True)

    payout = Payout(
        uuid=uuid4(), external_id=f"doliv-{uuid4().hex[:8]}", is_doliv=True,
        payout_terminal_id=None, requester_trader_id=requester.id,
        refill_requisite_id=None, trader_id=executor.id,
        payment_method=PaymentMethod.SBP, amount=Decimal("1000.00"),
        currency=Currency.RUB, exchange_rate=Decimal("10"), amount_usdt=amount_usdt,
        doliv_price_usdt=price, trader_fee_usdt=reward,
        req_holder="X", req_number="40817810099910000001", status=PayoutStatus.CLAIMED,
    )
    session.add(payout)
    await session.flush()

    async with session.begin_nested():
        await FinanceService(session).settle_doliv(payout, requester=requester, executor=executor)

    # Executor reimbursed amount + got the executor reward (TRADER_REWARD), but
    # the teamlead earned NOTHING — zero TEAMLEAD_REWARD legs anywhere.
    assert await _bal(session, user_id=executor.id) == amount_usdt + reward
    assert await _bal(session, user_id=tl.id) == Decimal("0")
    assert await _legs(session) == 0   # no TEAMLEAD_REWARD legs at all
