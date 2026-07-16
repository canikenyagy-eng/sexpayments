"""Integration tests for dispute-evidence access (DisputeService.*_evidence_*).

A dispute's evidence files are ordinary receipts carrying the ``dispute_id``.
These tests pin the role-scoped read rules:
  * admin   — sees every linked receipt;
  * trader  — own dispute only, premoderation-visible receipts only;
  * merchant— own dispute only, all linked receipts;
  * a receipt from another dispute is never reachable (uniform 404);
  * cross-scope access (wrong trader / wrong merchant) raises NotFound.
"""
from decimal import Decimal
from uuid import uuid4

import pytest

from app.common.enums.disputes import DisputeReason, DisputeStatus
from app.common.enums.finances import Currency
from app.common.enums.merchants import TerminalStatus
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.receipt_moderations import ModerationStatus
from app.common.enums.receipts import ReceiptSource
from app.common.enums.users import UserRole
from app.core.exceptions import NotFoundException
from app.core.security import get_password_hash
from app.modules.disputes.models import Dispute
from app.modules.disputes.service import DisputeService
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.receipts.models import Receipt
from app.modules.users.models import User


async def _mk_user(session, username, role=UserRole.TRADER) -> User:
    u = User(
        username=username, password=get_password_hash("pass12345"), role=role,
        totp_enabled=False, is_blocked=False, use_shared_balance=True,
    )
    session.add(u)
    await session.flush()
    return u


async def _mk_merchant(session, user_id, suffix) -> Merchant:
    m = Merchant(
        user_id=user_id, name=f"M-{suffix}", status=TerminalStatus.ENABLED,
        currency=Currency.RUB, api_key=f"key-{suffix}", api_secret=f"secret-{suffix}",
    )
    session.add(m)
    await session.flush()
    return m


async def _mk_order(session, merchant_id, trader_id) -> Order:
    o = Order(
        uuid=uuid4(), external_id=f"ext-{uuid4().hex[:8]}", merchant_id=merchant_id,
        trader_id=trader_id, direction=PaymentDirection.PAYIN, payment_method=PaymentMethod.SBP,
        amount=Decimal("1000.00"), currency=Currency.RUB, amount_usdt=Decimal("100"),
        status=OrderStatus.DISPUTED,
    )
    session.add(o)
    await session.flush()
    return o


async def _mk_dispute(session, order, merchant) -> Dispute:
    d = Dispute(
        order_id=order.id, merchant_id=merchant.id, initiator_type=UserRole.MERCHANT,
        status=DisputeStatus.OPEN, reason=DisputeReason.UNKNOWN,
        assigned_user_type=UserRole.TRADER, assigned_user_id=order.trader_id,
    )
    session.add(d)
    await session.flush()
    return d


async def _mk_receipt(session, order_id, dispute_id, *, status=ModerationStatus.APPROVED,
                      source=ReceiptSource.MERCHANT_WEB) -> Receipt:
    r = Receipt(
        uuid=uuid4(), order_id=order_id, dispute_id=dispute_id,
        file_path=f"/tmp/{uuid4().hex}.pdf", source=source, moderation_status=status,
    )
    session.add(r)
    await session.flush()
    return r


async def _setup(session):
    trader = await _mk_user(session, f"trader_{uuid4().hex[:6]}", role=UserRole.TRADER)
    mu = await _mk_user(session, f"mu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    merchant = await _mk_merchant(session, user_id=mu.id, suffix=uuid4().hex[:6])
    order = await _mk_order(session, merchant_id=merchant.id, trader_id=trader.id)
    dispute = await _mk_dispute(session, order, merchant)
    return trader, merchant, order, dispute


@pytest.mark.asyncio
async def test_admin_sees_all_evidence(session):
    trader, merchant, order, dispute = await _setup(session)
    await _mk_receipt(session, order.id, dispute.id, status=ModerationStatus.APPROVED)
    await _mk_receipt(session, order.id, dispute.id, status=ModerationStatus.PENDING)

    svc = DisputeService(session)
    rows = await svc.list_evidence_admin(dispute.id)
    assert len(rows) == 2  # admin sees pending too


@pytest.mark.asyncio
async def test_trader_sees_only_visible_evidence(session):
    trader, merchant, order, dispute = await _setup(session)
    approved = await _mk_receipt(session, order.id, dispute.id, status=ModerationStatus.APPROVED)
    await _mk_receipt(session, order.id, dispute.id, status=ModerationStatus.PENDING)

    svc = DisputeService(session)
    rows = await svc.list_evidence_trader(trader.id, dispute.uuid)
    assert [r.id for r in rows] == [approved.id]  # pending hidden from trader


@pytest.mark.asyncio
async def test_merchant_sees_all_own_evidence(session):
    trader, merchant, order, dispute = await _setup(session)
    await _mk_receipt(session, order.id, dispute.id, status=ModerationStatus.APPROVED)
    await _mk_receipt(session, order.id, dispute.id, status=ModerationStatus.PENDING)

    svc = DisputeService(session)
    rows = await svc.list_evidence_merchant(merchant, dispute.uuid)
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_admin_download_resolves_and_rejects_foreign(session):
    trader, merchant, order, dispute = await _setup(session)
    mine = await _mk_receipt(session, order.id, dispute.id)

    # A second dispute on a different order with its own receipt.
    trader2, merchant2, order2, dispute2 = await _setup(session)
    foreign = await _mk_receipt(session, order2.id, dispute2.id)

    svc = DisputeService(session)
    got = await svc.get_evidence_receipt_admin(dispute.id, str(mine.uuid))
    assert got.id == mine.id

    # A receipt that belongs to another dispute is a uniform 404.
    with pytest.raises(NotFoundException):
        await svc.get_evidence_receipt_admin(dispute.id, str(foreign.uuid))
    # Unknown uuid → 404.
    with pytest.raises(NotFoundException):
        await svc.get_evidence_receipt_admin(dispute.id, str(uuid4()))


@pytest.mark.asyncio
async def test_trader_download_hidden_when_not_visible(session):
    trader, merchant, order, dispute = await _setup(session)
    pending = await _mk_receipt(session, order.id, dispute.id, status=ModerationStatus.PENDING)

    svc = DisputeService(session)
    # Visible-only gate: a pending receipt is 404 for the trader …
    with pytest.raises(NotFoundException):
        await svc.get_evidence_receipt_trader(trader.id, dispute.uuid, str(pending.uuid))
    # … but reachable for the admin.
    got = await svc.get_evidence_receipt_admin(dispute.id, str(pending.uuid))
    assert got.id == pending.id


@pytest.mark.asyncio
async def test_cross_scope_access_is_denied(session):
    trader, merchant, order, dispute = await _setup(session)
    await _mk_receipt(session, order.id, dispute.id)

    other_trader = await _mk_user(session, f"other_{uuid4().hex[:6]}", role=UserRole.TRADER)
    other_mu = await _mk_user(session, f"omu_{uuid4().hex[:6]}", role=UserRole.MERCHANT)
    other_merchant = await _mk_merchant(session, user_id=other_mu.id, suffix=uuid4().hex[:6])

    svc = DisputeService(session)
    with pytest.raises(NotFoundException):
        await svc.list_evidence_trader(other_trader.id, dispute.uuid)
    with pytest.raises(NotFoundException):
        await svc.list_evidence_merchant(other_merchant, dispute.uuid)
