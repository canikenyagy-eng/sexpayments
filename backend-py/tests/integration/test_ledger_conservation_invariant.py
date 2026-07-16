"""
Flow-AGNOSTIC double-entry / value-conservation invariant harness.

Every money move in this system goes through ``FinanceService.transfer``
(``app/modules/finance/service.py:344``):

    if from_balance_id:  from_balance.amount -= amount     # debit
    if to_balance_id:    to_balance.amount   += amount      # credit
    ledger_repo.create({from_balance_id, to_balance_id, amount, ...})

So EVERY balance mutation is mirrored by exactly one ``LedgerEntry`` row whose
``from_balance_id`` / ``to_balance_id`` / ``amount`` encode the move. From that
single fact two invariants follow, and this file builds a *flow-agnostic*
harness that checks them after ANY sequence of real money flows:

  1. PER-ENTRY DOUBLE-ENTRY: each ledger row's effect on the global float is
     ``(+amount if to_balance set) + (-amount if from_balance set)``. An entry
     with BOTH balances set is global-neutral (an internal move); an entry with
     only ``to`` set is an external INFLOW (e.g. DEPOSIT — money minted in); one
     with only ``from`` set is an external OUTFLOW (e.g. WITHDRAWAL approve —
     money leaves the platform). Summed over all entries this gives the net
     change the ledger CLAIMS it applied to the global float.

  2. GLOBAL CONSERVATION: the actual ``SUM(balances.amount)`` now must equal
     ``seeded_total + Σ(inflow amounts) − Σ(outflow amounts)``. No money is
     minted or destroyed except through an explicit external in/out leg, and the
     amount minted/destroyed equals exactly what those legs recorded.

The two are cross-checked against each other: the *ledger-derived* net change
(invariant 1) must equal the *balance-observed* net change (invariant 2). If the
balances and the ledger ever disagree, the harness fails.

KEY app fact exercised: a full PAYIN ``complete_order`` settle keeps ALL money
inside the platform (trader ESCROW → merchant WORK → system → trader WORK), so a
settle is GLOBAL-NEUTRAL — no inflow/outflow legs. A WITHDRAWAL approve has a
``to_balance_id=None`` OUTFLOW leg (money leaves), and a DEPOSIT has a
``from_balance_id=None`` INFLOW leg. The CORRECT test drives all three real
flows (seed-as-deposit + payin settle + withdrawal approve) and asserts the
harness sees the books balanced. The ADVERSARIAL tests tamper with a balance /
ledger row out-of-band and assert the harness DETECTS the imbalance (teeth).

SQLite caveat: ``SELECT … FOR UPDATE`` in ``transfer`` is a no-op here, but
conservation/double-entry are pure arithmetic invariants that hold regardless of
locking — this harness is exactly the kind of check that's valid on SQLite.
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
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.finance.models import Balance, LedgerEntry
from app.modules.finance.schemas.admin import WithdrawalRequestCreate
from app.modules.finance.service import FinanceService
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.orders.service import OrderService
from app.modules.traders.models import Trader
from app.modules.users.models import User

CUR = Currency.USDT
DEST = "T-conservation-destination-xyz"
ADMIN_ID = 7777

# Canonical payin money shape (mirror of the settlement template).
AMOUNT = Decimal("100.0000")
FEE = Decimal("5.0000")
TRADER_FEE = Decimal("2.0000")


@pytest.fixture(autouse=True)
def _stub_celery():
    """Order completion + withdrawal hooks enqueue celery tasks; stub the
    broker on every import path the flows touch so nothing fires."""
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.modules.orders.service.celery_app", celery, create=True), \
         patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


# ══════════════════════════════════════════════════════════════════════
# THE HARNESS — flow-agnostic conservation + double-entry walkers
# ══════════════════════════════════════════════════════════════════════


async def _all_balances_total(session) -> Decimal:
    """SUM(amount) across EVERY balance row, all users/merchants/terminals/
    system, all types, all currencies — the global float."""
    total = (await session.execute(select(func.sum(Balance.amount)))).scalar_one()
    return Decimal(total) if total is not None else Decimal("0")


async def _ledger_external_flow(session) -> tuple[Decimal, Decimal]:
    """Walk EVERY ledger entry and split its effect on the global float:

      * ``from_balance_id IS NULL`` → external INFLOW of ``amount`` (mint).
      * ``to_balance_id   IS NULL`` → external OUTFLOW of ``amount`` (burn).
      * both set                    → internal, global-neutral (contributes 0).

    Returns ``(total_inflow, total_outflow)``. An entry can never have BOTH
    sides null (``transfer`` rejects it), so the split is unambiguous.
    """
    inflow = Decimal("0")
    outflow = Decimal("0")
    rows = (await session.execute(select(LedgerEntry))).scalars().all()
    for e in rows:
        if e.from_balance_id is None and e.to_balance_id is None:
            # Defensive: transfer() forbids this; if it ever appears the books
            # are already corrupt, so surface it loudly.
            raise AssertionError(f"ledger entry {e.id} has neither from nor to balance")
        if e.from_balance_id is None:
            inflow += e.amount
        if e.to_balance_id is None:
            outflow += e.amount
    return inflow, outflow


async def _ledger_net_global_delta(session) -> Decimal:
    """The net change to the global float that the LEDGER claims to have applied,
    derived purely from the entries: Σ over entries of
    ``(+amount if to set) + (-amount if from set)``. For an internal move the
    +amount and -amount cancel to 0; an inflow contributes +amount; an outflow
    contributes -amount. This is invariant (1)."""
    net = Decimal("0")
    rows = (await session.execute(select(LedgerEntry))).scalars().all()
    for e in rows:
        if e.to_balance_id is not None:
            net += e.amount
        if e.from_balance_id is not None:
            net -= e.amount
    return net


async def assert_conserved(session, *, seeded_total: Decimal) -> None:
    """THE core invariant assertion (flow-agnostic).

    After any sequence of real money flows:
      observed_total == seeded_total + inflow − outflow   (invariant 2)
    AND the ledger's own books agree with the balances:
      ledger_net_delta == observed_total − seeded_total   (invariant 1↔2)

    Raises ``AssertionError`` (with detail) on any mismatch — i.e. if money was
    minted or destroyed outside an explicit external leg, or if the balances and
    the ledger disagree about how much moved.
    """
    observed = await _all_balances_total(session)
    inflow, outflow = await _ledger_external_flow(session)
    expected = seeded_total + inflow - outflow
    assert observed == expected, (
        f"GLOBAL CONSERVATION VIOLATED: observed total {observed} != "
        f"seeded {seeded_total} + inflow {inflow} - outflow {outflow} = {expected}"
    )
    # Cross-check: the ledger-derived net delta must match the balance-observed
    # net delta. If a balance was tampered with WITHOUT a ledger row (or vice
    # versa) this catches the divergence even when (2) alone happens to net out.
    ledger_net = await _ledger_net_global_delta(session)
    observed_delta = observed - seeded_total
    assert ledger_net == observed_delta, (
        f"LEDGER↔BALANCE MISMATCH: ledger claims net move {ledger_net} but "
        f"balances moved {observed_delta} (observed {observed} - seeded {seeded_total})"
    )


# ── builders (self-contained, copied shape from the canonical templates) ──


async def _mk_user(session, *, role=UserRole.TRADER) -> User:
    u = User(
        username=f"u_{uuid4().hex[:8]}", password=get_password_hash("pass12345"),
        role=role, totp_enabled=False, is_blocked=False, use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_trader_profile(session, *, user_id) -> Trader:
    t = Trader(user_id=user_id, is_payin_active=True, withdrawal_fee_fixed=Decimal("0"))
    session.add(t)
    await session.flush()
    return t


async def _mk_merchant(session, *, user_id) -> Merchant:
    suffix = uuid4().hex[:6]
    m = Merchant(
        user_id=user_id, name=f"M-{suffix}", status=TerminalStatus.ENABLED,
        currency=Currency.RUB, api_key=f"key-{suffix}", api_secret=f"secret-{suffix}",
        fees={PaymentMethod.SBP.value: 5}, withdrawal_fee_fixed=Decimal("0"),
    )
    session.add(m)
    await session.flush()
    return m


async def _mk_order(session, *, merchant_id, trader_id, status) -> Order:
    o = Order(
        uuid=uuid4(), external_id=f"ext-{uuid4().hex[:8]}",
        merchant_id=merchant_id, trader_id=trader_id,
        direction=PaymentDirection.PAYIN, payment_method=PaymentMethod.SBP,
        amount=Decimal("1000.00"), currency=Currency.RUB, exchange_rate=Decimal("10"),
        amount_usdt=AMOUNT, fee_usdt=FEE, trader_fee_usdt=TRADER_FEE,
        profit_usdt=AMOUNT - FEE, status=status,
    )
    session.add(o)
    await session.flush()
    return o


async def _bal_row(session, *, user_id=None, merchant_id=None, is_system=False,
                   btype=BalanceType.WORK) -> Balance | None:
    stmt = select(Balance).where(Balance.type == btype, Balance.currency == CUR)
    if is_system:
        stmt = stmt.where(Balance.is_system.is_(True))
    elif user_id is not None:
        stmt = stmt.where(Balance.user_id == user_id)
    elif merchant_id is not None:
        stmt = stmt.where(Balance.merchant_id == merchant_id)
    return (await session.execute(stmt)).scalars().first()


# ── scenario driver ───────────────────────────────────────────────────


async def _seed_via_deposit(session, *, trader_work: Decimal, merchant_seed: Decimal):
    """Bring money INTO the system through the real ``deposit`` flow (inflow
    legs) so the seeded total is itself recorded in the ledger — the harness
    must account for it as inflow, never treat it as minted-from-nowhere.

    Returns (trader_user, merchant, seeded_total). ``seeded_total`` is the sum
    of the real external inflows we drove."""
    trader = await _mk_user(session, role=UserRole.TRADER)
    await _mk_trader_profile(session, user_id=trader.id)
    mu = await _mk_user(session, role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id)

    svc = FinanceService(session)
    if trader_work > 0:
        await svc.deposit(amount=trader_work, currency=CUR,
                          reference_id=f"seed-tr-{uuid4().hex[:6]}", user=trader)
    if merchant_seed > 0:
        await svc.deposit(amount=merchant_seed, currency=CUR,
                          reference_id=f"seed-me-{uuid4().hex[:6]}", merchant=merchant)
    return trader, merchant, (trader_work + merchant_seed)


# ══════════════════════════════════════════════════════════════════════
# CORRECT BEHAVIOUR — full payin settle + withdrawal across real flows
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_conserved_after_payin_settle_and_withdrawal(session):
    """End-to-end: seed (deposit), freeze a payin, settle it (global-neutral),
    then withdraw the trader's reward out of the platform. The harness asserts
    global conservation AND ledger↔balance agreement at every checkpoint, with
    the OUTFLOW leg of the withdrawal accounted for explicitly."""
    svc = FinanceService(session)
    trader, merchant, seeded = await _seed_via_deposit(
        session, trader_work=AMOUNT, merchant_seed=Decimal("0"),
    )
    # Seeded float fully recorded as inflow → conserved at the start.
    await assert_conserved(session, seeded_total=Decimal("0"))

    # FREEZE the collateral (trader WORK → ESCROW). Pure internal move.
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id,
                            status=OrderStatus.RECEIPT_UPLOADED)
    await svc.create_order(order=order, trader=trader)
    await assert_conserved(session, seeded_total=Decimal("0"))

    # SETTLE: trader ESCROW → merchant WORK → system → trader WORK. All internal
    # → the global float is UNCHANGED by a settle (nothing leaves on payin).
    await OrderService(session).complete_order(trader=trader, order_id=order.id)
    await assert_conserved(session, seeded_total=Decimal("0"))
    # Sanity on the distribution (matches the settlement template): merchant 95,
    # trader reward 2, system 3, escrow drained.
    assert (await _bal_row(session, merchant_id=merchant.id)).amount == AMOUNT - FEE
    assert (await _bal_row(session, user_id=trader.id)).amount == TRADER_FEE
    assert (await _bal_row(session, is_system=True)).amount == FEE - TRADER_FEE
    # Settle minted/destroyed nothing: still the full seeded float on the books.
    assert await _all_balances_total(session) == seeded

    # WITHDRAW the merchant's net (95) OUT of the platform — this is the only
    # leg that reduces the global float, and the harness must subtract it.
    req = await svc.create_withdrawal_request(
        WithdrawalRequestCreate(amount=AMOUNT - FEE, currency=CUR, destination_address=DEST),
        merchant=merchant,
    )
    await assert_conserved(session, seeded_total=Decimal("0"))  # freeze is internal
    await svc.approve_withdrawal_request(req.id, ADMIN_ID)
    # Now 95 has left the platform; conservation still holds via the outflow leg.
    await assert_conserved(session, seeded_total=Decimal("0"))
    assert await _all_balances_total(session) == seeded - (AMOUNT - FEE)


@pytest.mark.asyncio
async def test_each_ledger_entry_effect_is_double_entry_balanced(session):
    """Walk EVERY ledger entry after a full payin settle and assert each one is
    a well-formed double-entry row: it has at least one side, and the SUM of all
    entries' net global effect equals the balance-observed net change. Internal
    moves contribute 0; the only nonzero contributors are external in/out legs."""
    svc = FinanceService(session)
    trader, merchant, seeded = await _seed_via_deposit(
        session, trader_work=AMOUNT, merchant_seed=Decimal("0"),
    )
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id,
                            status=OrderStatus.PENDING)
    await svc.create_order(order=order, trader=trader)
    await OrderService(session).complete_order(trader=trader, order_id=order.id)

    rows = (await session.execute(select(LedgerEntry))).scalars().all()
    # Every entry has at least one balance side (no orphan rows).
    for e in rows:
        assert e.from_balance_id is not None or e.to_balance_id is not None
        assert e.amount > 0
    # The settle introduced ZERO external flow (all internal); only the seeding
    # deposit was inflow. So ledger-net == seeded float == observed total.
    inflow, outflow = await _ledger_external_flow(session)
    assert inflow == seeded and outflow == Decimal("0")
    assert await _ledger_net_global_delta(session) == seeded
    assert await _all_balances_total(session) == seeded
    # And the flow-agnostic harness agrees end to end.
    await assert_conserved(session, seeded_total=Decimal("0"))


@pytest.mark.asyncio
async def test_conserved_with_preseeded_balances_not_via_deposit(session):
    """The harness must also work when the float is seeded DIRECTLY as Balance
    rows (no inflow ledger legs) — here ``seeded_total`` is passed explicitly and
    a subsequent internal settle keeps it conserved with no external flow."""
    trader = await _mk_user(session, role=UserRole.TRADER)
    mu = await _mk_user(session, role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id)
    # Direct seed: trader holds AMOUNT in WORK, no ledger inflow recorded.
    b = Balance(user_id=trader.id, type=BalanceType.WORK, currency=CUR, amount=AMOUNT)
    session.add(b)
    await session.flush()
    seeded = AMOUNT

    await assert_conserved(session, seeded_total=seeded)  # no ledger yet → trivially holds

    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id,
                            status=OrderStatus.RECEIPT_UPLOADED)
    svc = FinanceService(session)
    await svc.create_order(order=order, trader=trader)
    await OrderService(session).complete_order(trader=trader, order_id=order.id)
    # Internal settle, no external flow → still exactly the seeded float.
    await assert_conserved(session, seeded_total=seeded)
    assert await _all_balances_total(session) == seeded


# ══════════════════════════════════════════════════════════════════════
# ADVERSARIAL — negative controls proving the harness has TEETH
# ══════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_harness_detects_minted_money_balance_credited_without_ledger(session):
    """Tamper: credit a balance directly (no ledger row) → money appears from
    nowhere. The conservation harness MUST flag it. We catch the AssertionError
    in a self-check to prove the harness fails as designed (teeth)."""
    trader = await _mk_user(session, role=UserRole.TRADER)
    b = Balance(user_id=trader.id, type=BalanceType.WORK, currency=CUR, amount=AMOUNT)
    session.add(b)
    await session.flush()
    seeded = AMOUNT
    # Clean state passes.
    await assert_conserved(session, seeded_total=seeded)

    # TAMPER: mint 50 onto the balance with NO ledger entry behind it.
    b.amount += Decimal("50")
    await session.flush()

    with pytest.raises(AssertionError, match="GLOBAL CONSERVATION VIOLATED"):
        await assert_conserved(session, seeded_total=seeded)


@pytest.mark.asyncio
async def test_harness_detects_ledger_without_balance_change(session):
    """Tamper the OTHER way: write a ledger entry that claims an external inflow
    but DON'T move any balance. The ledger now claims a net move the balances
    don't reflect → the ledger↔balance cross-check must fire."""
    trader = await _mk_user(session, role=UserRole.TRADER)
    b = Balance(user_id=trader.id, type=BalanceType.WORK, currency=CUR, amount=AMOUNT)
    session.add(b)
    await session.flush()
    seeded = AMOUNT
    await assert_conserved(session, seeded_total=seeded)

    # TAMPER: a phantom inflow ledger row (to_balance set, amount 30) but the
    # balance is NOT credited. observed_total is unchanged; the ledger claims
    # +30 inflow → expected total = seeded + 30 != observed → fails on (2),
    # and also the cross-check (1↔2) would diverge.
    phantom = LedgerEntry(
        from_balance_id=None, to_balance_id=b.id, amount=Decimal("30"), currency=CUR,
        reference_type=LedgerReferenceType.DEPOSIT, reference_id="phantom-inflow",
    )
    session.add(phantom)
    await session.flush()

    with pytest.raises(AssertionError):
        await assert_conserved(session, seeded_total=seeded)


@pytest.mark.asyncio
async def test_harness_detects_unbalanced_handbuilt_transfer(session):
    """A double-entry move done WRONG: debit one side but credit the other by a
    DIFFERENT amount (a hand-built non-conserving transfer). The total float
    silently changes with no external leg to justify it → harness flags it.

    This mimics a settle-path bug where the debit and credit legs disagree."""
    a = await _mk_user(session, role=UserRole.TRADER)
    c = await _mk_user(session, role=UserRole.TRADER)
    ba = Balance(user_id=a.id, type=BalanceType.WORK, currency=CUR, amount=AMOUNT)
    bc = Balance(user_id=c.id, type=BalanceType.WORK, currency=CUR, amount=Decimal("0"))
    session.add_all([ba, bc])
    await session.flush()
    seeded = AMOUNT
    await assert_conserved(session, seeded_total=seeded)

    # TAMPER: move 40 OUT of `a` but credit 45 INTO `c` (5 conjured), and write a
    # single internal ledger row claiming an internal (global-neutral) move of 40.
    ba.amount -= Decimal("40")
    bc.amount += Decimal("45")
    bad = LedgerEntry(
        from_balance_id=ba.id, to_balance_id=bc.id, amount=Decimal("40"), currency=CUR,
        reference_type=LedgerReferenceType.INTERNAL_TRANSFER, reference_id="bad-move",
    )
    session.add(bad)
    await session.flush()

    # The ledger row is "internal" (net 0) so it claims the float is unchanged,
    # but 5 was minted → observed total = seeded + 5, ledger-net = 0. Both the
    # conservation check and the cross-check disagree → AssertionError.
    with pytest.raises(AssertionError):
        await assert_conserved(session, seeded_total=seeded)


@pytest.mark.asyncio
async def test_harness_detects_destroyed_money(session):
    """Symmetric negative control: BURN money off a balance with no outflow
    ledger leg. observed_total drops below seeded with nothing to justify it."""
    trader = await _mk_user(session, role=UserRole.TRADER)
    b = Balance(user_id=trader.id, type=BalanceType.WORK, currency=CUR, amount=AMOUNT)
    session.add(b)
    await session.flush()
    seeded = AMOUNT
    await assert_conserved(session, seeded_total=seeded)

    b.amount -= Decimal("10")  # vanished, no ledger outflow behind it
    await session.flush()

    with pytest.raises(AssertionError, match="GLOBAL CONSERVATION VIOLATED"):
        await assert_conserved(session, seeded_total=seeded)


@pytest.mark.asyncio
async def test_harness_passes_on_legitimate_external_outflow(session):
    """Positive control for the negative controls: a LEGITIMATE withdrawal
    (which really does reduce the float, but via a proper outflow ledger leg)
    must NOT trip the harness — proving it flags only UNJUSTIFIED changes, not
    every change. Guards against a harness that's merely 'total must be constant'."""
    svc = FinanceService(session)
    trader, merchant, seeded = await _seed_via_deposit(
        session, trader_work=AMOUNT, merchant_seed=Decimal("0"),
    )
    req = await svc.create_withdrawal_request(
        WithdrawalRequestCreate(amount=Decimal("40"), currency=CUR, destination_address=DEST),
        user=trader,
    )
    await svc.approve_withdrawal_request(req.id, ADMIN_ID)
    # 40 legitimately left via an outflow leg; harness accounts for it and PASSES.
    await assert_conserved(session, seeded_total=Decimal("0"))
    assert await _all_balances_total(session) == seeded - Decimal("40")
