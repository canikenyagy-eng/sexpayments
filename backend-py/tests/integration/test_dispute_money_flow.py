"""
Integration tests for the simplified dispute money flow — against a REAL
in-memory ledger (no mocked finance).

Model recap (PAYIN):
  * Disputes open only on FINAL statuses (SUCCESS / FAILED / CANCELED).
  * On open, ``reconcile_for_dispute`` normalises the books into the
    "pending-like" frozen state: the trader's full collateral ends up in
    **trader ESCROW**, nothing settled / no fee / no reward outstanding —
    regardless of the prior status.
  * Two exits reuse the ordinary order flows:
      resolve → complete_order  (trader ESCROW → merchant WORK, fee, reward)
      reject  → cancel_order    (trader ESCROW → trader WORK)

These tests pin the exact final balances for the four (prior status × exit)
combinations plus the freeze-on-open intermediate state, and assert value
conservation (no money created or destroyed).
"""
from decimal import Decimal
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.common.enums.balances import BalanceType
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.users import UserRole
from app.core.security import get_password_hash
from app.modules.disputes.schemas import DisputeCreate
from app.common.enums.disputes import DisputeReason
from app.modules.disputes.service import DisputeService
from app.modules.finance.models import Balance
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.users.models import User

# Canonical amounts shared by every scenario.
AMOUNT = Decimal("100.0000")        # trader collateral / settled amount
FEE = Decimal("5.0000")             # system commission
TRADER_FEE = Decimal("2.0000")     # trader reward
NET = AMOUNT - FEE                  # 95 — what the merchant keeps on success


@pytest.fixture(autouse=True)
def _stub_celery():
    """Disputes enqueue notify/forward/webhook tasks — stub the broker."""
    notify = MagicMock()
    notify.apply_async = MagicMock(return_value=None)
    celery = MagicMock()
    celery.send_task = MagicMock(return_value=None)
    with patch("app.workers.tasks.trader_bot.notify_trader_new_dispute", notify, create=True), \
         patch("app.workers.celery_app.celery_app", celery, create=True):
        yield celery


# ── builders ──────────────────────────────────────────────────────────


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
        amount_usdt=AMOUNT,
        fee_usdt=FEE,
        trader_fee_usdt=TRADER_FEE,
        status=status,
    )
    session.add(o)
    await session.flush()
    return o


async def _bal(session, *, user_id=None, merchant_id=None, is_system=False, btype=BalanceType.WORK) -> Decimal:
    """Current amount of a balance, 0 if it was never created."""
    from sqlalchemy import select
    stmt = select(Balance).where(Balance.type == btype, Balance.currency == Currency.USDT)
    if is_system:
        stmt = stmt.where(Balance.is_system.is_(True))
    elif user_id is not None:
        stmt = stmt.where(Balance.user_id == user_id)
    elif merchant_id is not None:
        stmt = stmt.where(Balance.merchant_id == merchant_id)
    row = (await session.execute(stmt)).scalars().first()
    return row.amount if row else Decimal("0")


async def _setup(session, *, order_status, seed):
    """Create trader, merchant-user, merchant, order and seed WORK balances.

    ``seed`` maps a logical balance name → starting amount.
    """
    trader = await _mk_user(session, username=f"trader_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, username=f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])

    if seed.get("trader_work"):
        await _mk_balance(session, seed["trader_work"], user_id=trader.id)
    if seed.get("merchant_work"):
        await _mk_balance(session, seed["merchant_work"], merchant_id=merchant.id)
    if seed.get("system_work"):
        await _mk_balance(session, seed["system_work"], is_system=True)

    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id, status=order_status)
    return trader, merchant, order


def _total(*amounts) -> Decimal:
    return sum(amounts, Decimal("0"))


# Seeds reproducing the post-settlement balance shape for each prior status.
# SUCCESS: complete_order already ran → trader has reward, merchant has net,
#          system has (fee - reward).
SUCCESS_SEED = {"trader_work": TRADER_FEE, "merchant_work": NET, "system_work": FEE - TRADER_FEE}
# CANCELED/FAILED: cancel_order already ran → collateral back in trader WORK.
CANCELED_SEED = {"trader_work": AMOUNT}


# ── open: freeze to pending-like ───────────────────────────────────────


@pytest.mark.asyncio
async def test_open_on_success_freezes_full_amount_into_trader_escrow(session):
    trader, merchant, order = await _setup(session, order_status=OrderStatus.SUCCESS, seed=SUCCESS_SEED)
    service = DisputeService(session)

    await service.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.UNKNOWN),
        order_id=str(order.uuid),
    )

    # Pending-like: full collateral in trader ESCROW, everything else drained.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.WORK) == Decimal("0")
    assert await _bal(session, merchant_id=merchant.id, btype=BalanceType.WORK) == Decimal("0")
    assert await _bal(session, is_system=True, btype=BalanceType.WORK) == Decimal("0")
    # Order is DISPUTED.
    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.DISPUTED


@pytest.mark.asyncio
async def test_open_on_canceled_refreezes_collateral_into_trader_escrow(session):
    trader, merchant, order = await _setup(session, order_status=OrderStatus.CANCELED, seed=CANCELED_SEED)
    service = DisputeService(session)

    await service.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.NO_PAYMENT),
        order_id=str(order.uuid),
    )

    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.WORK) == Decimal("0")


# ── admin-initiated open (open_dispute_by_admin) ───────────────────────


@pytest.mark.asyncio
async def test_admin_open_dispute_reconciles_and_marks_initiator(session):
    """Admin opens a dispute on any order by UUID — same money reconcile as the
    merchant path, but attributed to the admin and merchant derived from order."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.SUCCESS, seed=SUCCESS_SEED)
    service = DisputeService(session)

    dispute = await service.open_dispute_by_admin(
        admin_id=99, reason=DisputeReason.NO_PAYMENT, order_uuid=str(order.uuid),
    )

    # Same freeze-to-pending shape: full collateral in trader ESCROW.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == AMOUNT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.WORK) == Decimal("0")
    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.DISPUTED
    # Attributed to the admin; merchant derived from the order.
    assert dispute.initiator_type == UserRole.ADMIN and dispute.initiator_id == 99
    assert dispute.merchant_id == merchant.id


@pytest.mark.asyncio
async def test_admin_open_dispute_with_file_stores_system_receipt(session, tmp_path, monkeypatch):
    """Admin attaches a real file → a dispute-linked receipt is stored with
    source=SYSTEM (system-initiated), through the same upload pipeline."""
    monkeypatch.setattr("app.modules.receipts.storage.settings.UPLOAD_DIR", str(tmp_path), raising=False)
    # Receipt upload's approved-effects fire via effects.celery_app (bound at
    # import, so the autouse module-attr patch doesn't reach it) — stub it here
    # so the test doesn't block on a real broker connection.
    monkeypatch.setattr("app.modules.receipts.effects.celery_app", MagicMock())

    trader, merchant, order = await _setup(session, order_status=OrderStatus.SUCCESS, seed=SUCCESS_SEED)
    service = DisputeService(session)

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    dispute = await service.open_dispute_by_admin(
        admin_id=7, reason=DisputeReason.HAS_PAYMENT, order_uuid=str(order.uuid),
        attachments=[(png, "proof.png")],
    )

    from app.common.enums.receipts import ReceiptSource
    from app.modules.receipts.repository import ReceiptRepository

    rows = await ReceiptRepository(session).list_for_dispute(dispute.id)
    assert len(rows) == 1
    assert rows[0].source == ReceiptSource.SYSTEM
    assert rows[0].dispute_id == dispute.id
    # Stored path mirrored onto the dispute (trusted, in UPLOAD_DIR).
    refreshed_dispute = await session.get(type(dispute), dispute.id)
    assert refreshed_dispute.evidence_files == [rows[0].file_path]


@pytest.mark.asyncio
async def test_reattach_accumulates_evidence_files(session, tmp_path, monkeypatch):
    """A 2nd file re-attached to an OPEN dispute via the additive create endpoint
    APPENDS to evidence_files (never truncates the first) and evidence_count
    reflects both — locks the shared _mirror_dispute_evidence_files accumulation
    invariant against a replace/truncate regression, with real file I/O."""
    monkeypatch.setattr("app.modules.receipts.storage.settings.UPLOAD_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr("app.modules.receipts.effects.celery_app", MagicMock())

    trader, merchant, order = await _setup(session, order_status=OrderStatus.SUCCESS, seed=SUCCESS_SEED)
    service = DisputeService(session)

    png1 = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    png2 = b"\x89PNG\r\n\x1a\n" + b"\x11" * 48  # distinct sha

    first = await service.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.HAS_PAYMENT),
        order_id=str(order.uuid), attachments=[(png1, "proof1.png")],
    )
    refreshed = await session.get(type(first), first.id)
    assert len(refreshed.evidence_files) == 1
    first_path = refreshed.evidence_files[0]

    # 2nd open on the same order = additive re-attach of a distinct file.
    second = await service.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.HAS_PAYMENT),
        order_id=str(order.uuid), attachments=[(png2, "proof2.png")],
    )

    assert second.id == first.id  # same dispute, appended (not a new dispute)
    from app.modules.receipts.repository import ReceiptRepository
    rows = await ReceiptRepository(session).list_for_dispute(first.id)
    assert len(rows) == 2  # both receipts linked to the dispute
    refreshed2 = await session.get(type(first), first.id)
    assert len(refreshed2.evidence_files) == 2     # accumulated, NOT replaced
    assert first_path in refreshed2.evidence_files  # the first file survived

    from app.modules.disputes.schemas import DisputeMerchantResponse
    resp = DisputeMerchantResponse.model_validate(refreshed2, from_attributes=True)
    assert resp.evidence_count == 2  # merchant-facing count reflects both


# ── full lifecycle: 4 combinations of (prior status × exit) ────────────


@pytest.mark.asyncio
async def test_success_then_resolve_is_a_noop_roundtrip(session):
    """Merchant disputes a paid order and wins → ends exactly where it started."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.SUCCESS, seed=SUCCESS_SEED)
    service = DisputeService(session)

    dispute = await service.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.UNKNOWN),
        order_id=str(order.uuid),
    )
    await service.resolve_dispute(admin_id=1, dispute_id=dispute.id, resolution_text="merchant wins")

    # Back to the post-success shape — merchant keeps net, trader keeps reward.
    assert await _bal(session, merchant_id=merchant.id) == NET
    assert await _bal(session, user_id=trader.id) == TRADER_FEE
    assert await _bal(session, is_system=True) == FEE - TRADER_FEE
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.SUCCESS


@pytest.mark.asyncio
async def test_success_then_reject_moves_everything_to_trader(session):
    """Merchant disputes a paid order and loses → trader gets the full amount."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.SUCCESS, seed=SUCCESS_SEED)
    service = DisputeService(session)

    dispute = await service.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.UNKNOWN),
        order_id=str(order.uuid),
    )
    await service.reject_dispute(admin_id=1, dispute_id=dispute.id, resolution_text="trader wins")

    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")
    assert await _bal(session, is_system=True) == Decimal("0")
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.FAILED
    # Financial snapshot zeroed on reject (settlement reversed).
    assert refreshed.teamlead_reward_usdt == Decimal("0")
    assert refreshed.platform_profit_usdt == Decimal("0")
    assert refreshed.financials["settled"] is False


@pytest.mark.asyncio
async def test_canceled_then_resolve_settles_to_merchant(session):
    """Merchant disputes a canceled order and wins → settles like a success."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.CANCELED, seed=CANCELED_SEED)
    service = DisputeService(session)

    dispute = await service.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.NO_PAYMENT),
        order_id=str(order.uuid),
    )
    await service.resolve_dispute(admin_id=1, dispute_id=dispute.id, resolution_text="merchant wins")

    assert await _bal(session, merchant_id=merchant.id) == NET
    assert await _bal(session, user_id=trader.id) == TRADER_FEE
    assert await _bal(session, is_system=True) == FEE - TRADER_FEE
    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.SUCCESS
    # Financial snapshot finalized on resolve→success (no teamleads ⇒ profit = fee − trader_fee).
    assert refreshed.teamlead_reward_usdt == Decimal("0")
    assert refreshed.platform_profit_usdt == FEE - TRADER_FEE
    assert refreshed.financials["settled"] is True


@pytest.mark.asyncio
async def test_canceled_then_reject_is_a_noop_roundtrip(session):
    """Merchant disputes a canceled order and loses → trader keeps collateral."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.CANCELED, seed=CANCELED_SEED)
    service = DisputeService(session)

    dispute = await service.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.NO_PAYMENT),
        order_id=str(order.uuid),
    )
    await service.reject_dispute(admin_id=1, dispute_id=dispute.id, resolution_text="trader wins")

    assert await _bal(session, user_id=trader.id) == AMOUNT
    assert await _bal(session, merchant_id=merchant.id) == Decimal("0")
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.FAILED


# ── value conservation across the whole flow ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("order_status,seed", [
    (OrderStatus.SUCCESS, SUCCESS_SEED),
    (OrderStatus.CANCELED, CANCELED_SEED),
])
async def test_total_value_conserved_through_dispute(session, order_status, seed):
    """No money is created or destroyed by opening + resolving a dispute."""
    trader, merchant, order = await _setup(session, order_status=order_status, seed=seed)

    before = _total(
        await _bal(session, user_id=trader.id),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, is_system=True),
    )
    assert before == AMOUNT  # sanity: each seed totals the order amount

    service = DisputeService(session)
    dispute = await service.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.UNKNOWN),
        order_id=str(order.uuid),
    )
    await service.resolve_dispute(admin_id=1, dispute_id=dispute.id, resolution_text="ok")

    after = _total(
        await _bal(session, user_id=trader.id),
        await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
        await _bal(session, merchant_id=merchant.id),
        await _bal(session, is_system=True),
    )
    assert after == before


@pytest.mark.asyncio
async def test_force_status_cycle_conserves_money_with_teamlead(session):
    """Cycle an order through terminal statuses via the admin force-override
    (``change_status(force=True)`` — the support-panel "change status" path) and
    assert NO money leaks for trader / merchant / system / **teamlead**, with a
    real non-zero teamlead reward in play.

    The order is driven through a REAL ``complete_order`` first (so genuine
    teamlead-reward ledger entries exist), then cycled. Every terminal→terminal
    force move FULLY reverses the prior settlement — undoes trader reward, system
    fee, merchant credit, **teamlead reward** (``reverse_rewards``, ledger-based +
    repeat-safe) + turnover, back to the frozen ESCROW baseline — then applies the
    new exit. So the cycle is idempotent: after every step the five balances must
    match the exact shape for the CURRENT status and the grand total must hold.
    """
    from app.common.enums.users import UserRole
    from app.modules.orders.service import OrderService
    from app.modules.teamleaders.models import TeamleadLink

    # trader + merchant + a teamlead linked to the trader at 1%.
    trader, merchant, order = await _setup(
        session, order_status=OrderStatus.PENDING, seed={}
    )
    teamlead = await _mk_user(session, username=f"tl_{uuid4().hex[:6]}", role=UserRole.TEAMLEAD)
    session.add(TeamleadLink(
        teamlead_id=teamlead.id,
        linked_entity_type=UserRole.TRADER,
        linked_entity_id=trader.id,
        fee_percent=Decimal("1.00"),
        is_active=True,
    ))
    # Collateral is frozen in trader ESCROW (the pending-like baseline).
    await _mk_balance(session, AMOUNT, user_id=trader.id, btype=BalanceType.ESCROW)
    await session.flush()

    svc = OrderService(session)
    TL_REWARD = Decimal("1.0000")  # 1% of amount_usdt (100)
    Z = Decimal("0")

    async def _shape() -> dict:
        return {
            "trader_work": await _bal(session, user_id=trader.id, btype=BalanceType.WORK),
            "trader_escrow": await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW),
            "merchant_work": await _bal(session, merchant_id=merchant.id, btype=BalanceType.WORK),
            "system_work": await _bal(session, is_system=True, btype=BalanceType.WORK),
            "teamlead_work": await _bal(session, user_id=teamlead.id, btype=BalanceType.WORK),
        }

    SUCCESS_SHAPE = {
        "trader_work": TRADER_FEE, "trader_escrow": Z,
        "merchant_work": NET,
        "system_work": FEE - TRADER_FEE - TL_REWARD,  # system keeps fee minus both rewards
        "teamlead_work": TL_REWARD,
    }
    RELEASED_SHAPE = {  # FAILED / CANCELED — everything back to trader WORK
        "trader_work": AMOUNT, "trader_escrow": Z,
        "merchant_work": Z, "system_work": Z, "teamlead_work": Z,
    }
    EXPECTED = {
        OrderStatus.SUCCESS: SUCCESS_SHAPE,
        OrderStatus.FAILED: RELEASED_SHAPE,
        OrderStatus.CANCELED: RELEASED_SHAPE,
        # REFUNDED = admin full unwind ("as if the order never happened") —
        # money-wise identical to CANCELED: collateral back in trader WORK.
        OrderStatus.REFUNDED: RELEASED_SHAPE,
    }
    total = AMOUNT  # the whole order is exactly the collateral

    # PENDING → SUCCESS via REAL complete_order (pays the teamlead, writes ledger).
    order = await svc.change_status(
        order, OrderStatus.SUCCESS, actor_id=1, audit_action=None,
        fire_callback=False, force=True,
    )
    first = await _shape()
    assert first == SUCCESS_SHAPE, f"post-settle shape wrong: {first}"
    assert _total(*first.values()) == total

    # Now cycle around the terminal states several times via the admin override,
    # including REFUNDED (SUCCESS→REFUNDED unwind, then REFUNDED→SUCCESS re-settle).
    for target in [
        OrderStatus.FAILED, OrderStatus.SUCCESS, OrderStatus.REFUNDED,
        OrderStatus.SUCCESS, OrderStatus.CANCELED, OrderStatus.SUCCESS,
        OrderStatus.REFUNDED,
    ]:
        order = await svc.change_status(
            order, target, actor_id=1, reason="cycle", audit_action=None,
            fire_callback=False, force=True,
        )
        bals = await _shape()
        assert order.status == target
        assert bals == EXPECTED[target], f"balances wrong after → {target.value}: {bals}"
        # Conservation: money only moves between buckets, never created/destroyed.
        assert _total(*bals.values()) == total, f"total drifted after → {target.value}"


# ── guards that depend on real DB state ────────────────────────────────


@pytest.mark.asyncio
async def test_reopen_dispute_is_additive_no_double_reconcile(session):
    """A 2nd open on the same order is ADDITIVE, not an error: the same open
    dispute is returned (the merchant can keep attaching checks), the order stays
    DISPUTED, and NO money moves again — the re-attach path never re-runs the
    dispute reconcile (which would double-charge the trader)."""
    trader, merchant, order = await _setup(session, order_status=OrderStatus.CANCELED, seed=CANCELED_SEED)
    service = DisputeService(session)

    first = await service.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.UNKNOWN),
        order_id=str(order.uuid),
    )
    work_after_first = await _bal(session, user_id=trader.id, btype=BalanceType.WORK)
    escrow_after_first = await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW)

    # Order is now DISPUTED; a second open re-attaches instead of erroring.
    second = await service.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.UNKNOWN),
        order_id=str(order.uuid),
    )

    assert second.id == first.id  # same dispute, no duplicate / no error
    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.DISPUTED
    # No second reconcile — the re-open left the trader's balances untouched.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.WORK) == work_after_first
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == escrow_after_first


@pytest.mark.asyncio
async def test_open_dispute_on_active_pending_is_allowed_and_does_not_refreeze(session):
    """PENDING (active) orders ARE disputable now. The collateral is already in
    ESCROW from order creation, so reconcile is a NO-OP for an active pre-status:
    opening the dispute must NOT pull the trader's WORK into ESCROW again (that
    would double-charge). The order moves to DISPUTED."""
    trader, merchant, order = await _setup(
        session, order_status=OrderStatus.PENDING, seed={"trader_work": AMOUNT}
    )
    service = DisputeService(session)

    await service.open_dispute_by_merchant(
        merchant, DisputeCreate(reason=DisputeReason.UNKNOWN),
        order_id=str(order.uuid),
    )

    # No-op reconcile: trader WORK untouched, nothing re-frozen into ESCROW.
    assert await _bal(session, user_id=trader.id, btype=BalanceType.WORK) == AMOUNT
    assert await _bal(session, user_id=trader.id, btype=BalanceType.ESCROW) == Decimal("0")
    refreshed = await session.get(Order, order.id)
    assert refreshed.status == OrderStatus.DISPUTED


@pytest.mark.asyncio
async def test_cannot_open_dispute_on_created_order(session):
    """CREATED is the one remaining non-disputable status (no requisite issued
    yet → no trader to assign the dispute to)."""
    from app.core.exceptions import ValidationException
    trader, merchant, order = await _setup(session, order_status=OrderStatus.CREATED, seed={})
    service = DisputeService(session)

    with pytest.raises(ValidationException, match="Cannot open dispute for order in status created"):
        await service.open_dispute_by_merchant(
            merchant, DisputeCreate(reason=DisputeReason.UNKNOWN),
            order_id=str(order.uuid),
        )
