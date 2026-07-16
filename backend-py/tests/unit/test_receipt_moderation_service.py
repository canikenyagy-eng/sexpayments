"""Unit tests for ReceiptModerationService.

Covers:
  * create_pending — writes a row + transitions the order to PENDING
  * apply_decision (happy path) — sets decision, updates order status
  * apply_decision (race lost) — atomic precondition rejects second click
  * apply_decision (missing row) — defensive NotFound path
"""
from datetime import datetime, timezone

import pytest

from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.receipt_moderations import (
    ModerationDecision,
    ModerationStatus,
)
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.receipts.exceptions import (
    ModerationAlreadyDecidedError,
    ModerationRowNotFoundError,
)
from app.modules.receipts.models import ReceiptModeration
from app.modules.receipts.moderation import ReceiptModerationService


async def _make_merchant(session) -> Merchant:
    merchant = Merchant(
        user_id=1,
        api_key="k",
        api_secret="s",
        currency=Currency.RUB,
        fees={},
    )
    session.add(merchant)
    await session.flush()
    return merchant


async def _make_order(session, merchant: Merchant) -> Order:
    order = Order(
        external_id="ext-1",
        merchant_id=merchant.id,
        direction=PaymentDirection.PAYIN,
        payment_method=PaymentMethod.SBP,
        amount=100,
        currency=Currency.RUB,
        status=OrderStatus.RECEIPT_UPLOADED,
        moderation_status=ModerationStatus.NONE,
    )
    session.add(order)
    await session.flush()
    return order


@pytest.mark.asyncio
async def test_create_pending_writes_row_and_transitions_order(session):
    merchant = await _make_merchant(session)
    order = await _make_order(session, merchant)

    service = ReceiptModerationService(session)
    row = await service.create_pending(order, chat_id=-100123)

    assert row.order_id == order.id
    assert row.chat_id == -100123
    assert row.decision is None
    assert order.moderation_status == ModerationStatus.PENDING


@pytest.mark.asyncio
async def test_apply_decision_happy_path(session):
    merchant = await _make_merchant(session)
    order = await _make_order(session, merchant)
    service = ReceiptModerationService(session)
    await service.create_pending(order, chat_id=-100123)

    row = await service.apply_decision(
        order=order,
        decision=ModerationDecision.ACCEPT,
        moderator_tg_id=42,
        moderator_username="alice",
        message_id=777,
    )

    assert row.decision == ModerationDecision.ACCEPT
    assert row.moderator_tg_id == 42
    assert row.moderator_username == "alice"
    assert row.message_id == 777
    assert isinstance(row.decided_at, datetime)
    assert order.moderation_status == ModerationStatus.APPROVED


@pytest.mark.asyncio
async def test_apply_decision_pdf_request_maps_to_pdf_requested_status(session):
    merchant = await _make_merchant(session)
    order = await _make_order(session, merchant)
    service = ReceiptModerationService(session)
    await service.create_pending(order, chat_id=1)

    row = await service.apply_decision(
        order=order,
        decision=ModerationDecision.REQUEST_PDF,
        moderator_tg_id=1,
        moderator_username="bob",
        message_id=None,
    )

    assert row.decision == ModerationDecision.REQUEST_PDF
    assert order.moderation_status == ModerationStatus.PDF_REQUESTED


@pytest.mark.asyncio
async def test_apply_decision_first_wins_rejects_second_call(session):
    merchant = await _make_merchant(session)
    order = await _make_order(session, merchant)
    service = ReceiptModerationService(session)
    await service.create_pending(order, chat_id=1)

    # First click — succeeds.
    await service.apply_decision(
        order=order,
        decision=ModerationDecision.ACCEPT,
        moderator_tg_id=1,
        moderator_username="alice",
        message_id=None,
    )
    assert order.moderation_status == ModerationStatus.APPROVED

    # Second click on the same order — the WHERE precondition
    # (moderation_status='pending') no longer matches, so rowcount=0 and
    # the service raises.
    with pytest.raises(ModerationAlreadyDecidedError):
        await service.apply_decision(
            order=order,
            decision=ModerationDecision.REQUEST_PDF,
            moderator_tg_id=2,
            moderator_username="bob",
            message_id=None,
        )


@pytest.mark.asyncio
async def test_apply_decision_without_pending_row_raises(session):
    """Defensive: if the order is left in PENDING but no row exists (manual
    DB tinkering), the service raises NotFound rather than silently
    corrupting state."""
    merchant = await _make_merchant(session)
    order = await _make_order(session, merchant)
    # Skip create_pending — set status manually so the precondition matches.
    order.moderation_status = ModerationStatus.PENDING
    session.add(order)
    await session.flush()

    service = ReceiptModerationService(session)
    with pytest.raises(ModerationRowNotFoundError):
        await service.apply_decision(
            order=order,
            decision=ModerationDecision.ACCEPT,
            moderator_tg_id=1,
            moderator_username=None,
            message_id=None,
        )


@pytest.mark.asyncio
async def test_set_message_id_backfills(session):
    merchant = await _make_merchant(session)
    order = await _make_order(session, merchant)
    service = ReceiptModerationService(session)
    row = await service.create_pending(order, chat_id=1)
    assert row.message_id is None

    await service.set_message_id(row.id, message_id=12345)

    refreshed = await session.get(ReceiptModeration, row.id)
    assert refreshed.message_id == 12345


@pytest.mark.asyncio
async def test_apply_decision_flips_pending_receipts(session):
    """ACCEPT flips this order's PENDING receipts to APPROVED so the per-receipt
    trader-visibility gate stays in sync; receipts of other orders are
    untouched."""
    from app.common.enums.receipts import ReceiptSource
    from app.modules.receipts.models import Receipt
    from app.modules.receipts.service import ReceiptService

    merchant = await _make_merchant(session)
    order = await _make_order(session, merchant)
    other = await _make_order(session, merchant)

    rsvc = ReceiptService(session)
    r1 = await rsvc.add_receipt(order_id=order.id, file_path="a.pdf",
                                source=ReceiptSource.MERCHANT_API, sha256="a",
                                moderation_status=ModerationStatus.PENDING)
    r_other = await rsvc.add_receipt(order_id=other.id, file_path="b.pdf",
                                     source=ReceiptSource.MERCHANT_API, sha256="b",
                                     moderation_status=ModerationStatus.PENDING)

    service = ReceiptModerationService(session)
    await service.create_pending(order, chat_id=-100123)
    await service.apply_decision(
        order=order, decision=ModerationDecision.ACCEPT,
        moderator_tg_id=1, moderator_username="a", message_id=1,
    )

    assert (await session.get(Receipt, r1.id)).moderation_status == ModerationStatus.APPROVED
    # The other order's receipt is NOT touched.
    assert (await session.get(Receipt, r_other.id)).moderation_status == ModerationStatus.PENDING
