"""Integration tests for the unified receipts store against a real (SQLite)
session — ReceiptService + ReceiptRepository exercised with actual inserts and
queries, not mocks. Covers the dedup/cap invariants and the trader-visibility
filter (a security boundary).
"""
import itertools

import pytest

from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.receipt_moderations import ModerationStatus
from app.common.enums.receipts import ReceiptSource, ReceiptUploader
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.receipts.exceptions import DuplicateReceiptError, ReceiptLimitReachedError
from app.modules.receipts.models import Receipt
from app.modules.receipts.repository import ReceiptRepository
from app.modules.receipts.service import ReceiptService


_seq = itertools.count(1)

# Minimal valid PDF — confirm_order format-gates the uploaded file (ext + magic byte).
_VALID_PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\n%%EOF"


async def _make_order(session, *, trader_id=None, status=OrderStatus.RECEIPT_UPLOADED) -> Order:
    n = next(_seq)
    merchant = Merchant(user_id=n, api_key=f"k{n}", api_secret=f"s{n}", currency=Currency.RUB, fees={})
    session.add(merchant)
    await session.flush()
    order = Order(
        external_id=f"ext-{n}", merchant_id=merchant.id, trader_id=trader_id,
        direction=PaymentDirection.PAYIN, payment_method=PaymentMethod.SBP,
        amount=100, currency=Currency.RUB, status=status,
        moderation_status=ModerationStatus.NONE,
    )
    session.add(order)
    await session.flush()
    return order


@pytest.mark.asyncio
async def test_add_receipt_persists_and_lists(session):
    order = await _make_order(session)
    svc = ReceiptService(session)

    r = await svc.add_receipt(
        order_id=order.id, file_path="uploads/a.pdf",
        source=ReceiptSource.MERCHANT_API, uploaded_by="merchant", sha256="aa",
    )
    assert r.id is not None and r.uuid is not None
    rows = await svc.list_for_order(order.id)
    assert [x.file_path for x in rows] == ["uploads/a.pdf"]


@pytest.mark.asyncio
async def test_add_receipt_dedup_rejects_same_sha(session):
    order = await _make_order(session)
    svc = ReceiptService(session)
    await svc.add_receipt(order_id=order.id, file_path="a.pdf",
                          source=ReceiptSource.MERCHANT_API, sha256="dup")
    with pytest.raises(DuplicateReceiptError):
        await svc.add_receipt(order_id=order.id, file_path="a2.pdf",
                              source=ReceiptSource.MERCHANT_API, sha256="dup")
    # Only one row stored.
    assert await ReceiptRepository(session).count_for_order(order.id) == 1


@pytest.mark.asyncio
async def test_add_receipt_same_sha_different_order_is_allowed(session):
    o1 = await _make_order(session)
    o2 = await _make_order(session)
    svc = ReceiptService(session)
    await svc.add_receipt(order_id=o1.id, file_path="a.pdf",
                          source=ReceiptSource.MERCHANT_API, sha256="same")
    # Same hash on a DIFFERENT order is fine — dedup is per order.
    r = await svc.add_receipt(order_id=o2.id, file_path="a.pdf",
                              source=ReceiptSource.MERCHANT_API, sha256="same")
    assert r.order_id == o2.id


@pytest.mark.asyncio
async def test_add_receipt_enforces_cap(session):
    order = await _make_order(session)
    svc = ReceiptService(session)
    for i in range(ReceiptService.MAX_PER_ORDER):
        await svc.add_receipt(order_id=order.id, file_path=f"r{i}.pdf",
                              source=ReceiptSource.SYSTEM, sha256=f"h{i}")
    with pytest.raises(ReceiptLimitReachedError):
        await svc.add_receipt(order_id=order.id, file_path="overflow.pdf",
                              source=ReceiptSource.SYSTEM, sha256="hX")


@pytest.mark.asyncio
async def test_list_for_order_visible_filter_hides_pending(session):
    """Security boundary: a trader must NOT see receipts still in premoderation
    (PENDING / pdf_requested / video_requested); NONE and APPROVED are visible."""
    order = await _make_order(session)
    svc = ReceiptService(session)
    await svc.add_receipt(order_id=order.id, file_path="none.pdf",
                          source=ReceiptSource.MERCHANT_API, sha256="1",
                          moderation_status=ModerationStatus.NONE)
    await svc.add_receipt(order_id=order.id, file_path="approved.pdf",
                          source=ReceiptSource.MERCHANT_API, sha256="2",
                          moderation_status=ModerationStatus.APPROVED)
    await svc.add_receipt(order_id=order.id, file_path="pending.pdf",
                          source=ReceiptSource.MERCHANT_API, sha256="3",
                          moderation_status=ModerationStatus.PENDING)

    all_rows = await svc.list_for_order(order.id)
    visible = await svc.list_for_order(order.id, visible_to_trader_only=True)
    assert len(all_rows) == 3
    paths = {r.file_path for r in visible}
    assert paths == {"none.pdf", "approved.pdf"}   # pending.pdf hidden
    assert "pending.pdf" not in paths


@pytest.mark.asyncio
async def test_list_for_dispute_and_get_by_uuid(session):
    order = await _make_order(session)
    svc = ReceiptService(session)
    r_plain = await svc.add_receipt(order_id=order.id, file_path="plain.pdf",
                                    source=ReceiptSource.MERCHANT_API, sha256="p")
    r_appeal = await svc.add_receipt(order_id=order.id, file_path="appeal.pdf",
                                     source=ReceiptSource.DISPUTE_BOT, sha256="ap",
                                     dispute_id=555)

    appeal_rows = await svc.list_for_dispute(555)
    assert [r.file_path for r in appeal_rows] == ["appeal.pdf"]

    got = await svc.get_by_uuid(str(r_plain.uuid))
    assert got is not None and got.id == r_plain.id
    assert await svc.get_by_uuid("not-a-uuid") is None


# ── confirm_order end-to-end (real session, real receipt row + mirror) ──────
# Proves the full multi-receipt flow through OrderService.confirm_order: file
# saved, receipt appended to the unified store with the dispute link, and the
# legacy orders.receipt_file mirror updated.


@pytest.mark.asyncio
async def test_confirm_order_stores_receipt_and_updates_mirror(session, tmp_path, monkeypatch):
    import hashlib
    import os
    from unittest.mock import AsyncMock, MagicMock

    from app.common.enums.receipt_moderations import ModerationStatus as _MS
    from app.modules.orders import service as svc_mod
    from app.modules.orders.service import OrderService

    monkeypatch.setattr(svc_mod.settings, "UPLOAD_DIR", str(tmp_path), raising=False)
    # Approved path enqueues notify/fraud/cascade via celery_app.send_task — stub
    # the broker boundary so the test doesn't block on a real connection.
    monkeypatch.setattr("app.workers.celery_app.celery_app.send_task", MagicMock())

    order = await _make_order(session, status=OrderStatus.PENDING)
    merchant = await session.get(Merchant, order.merchant_id)

    # Premoderation off → receipt auto-approves (PENDING → RECEIPT_UPLOADED).
    # Patch the single source of truth (the receipts policy), not OrderService.
    from app.modules.receipts.moderation import ReceiptModerationPolicy
    monkeypatch.setattr(
        ReceiptModerationPolicy, "is_enabled_for",
        AsyncMock(return_value=False), raising=True,
    )

    svc = OrderService(session)

    att = AsyncMock()
    att.read = AsyncMock(return_value=_VALID_PDF)  # confirm_order format-gates
    att.filename = "r.pdf"

    out = await svc.confirm_order(
        merchant=merchant, attachment=att, order_id=str(order.uuid),
        uploaded_by=ReceiptUploader.MERCHANT_DISPUTE_BOT, dispute_id=42,
    )

    # Mirror updated to the saved file; PENDING → RECEIPT_UPLOADED.
    assert out.status == OrderStatus.RECEIPT_UPLOADED
    assert out.receipt_file and out.receipt_file.startswith(str(tmp_path))
    assert os.path.exists(out.receipt_file)

    # Real receipt row exists, linked to the dispute, channel=dispute_bot.
    rows = await ReceiptService(session).list_for_order(order.id)
    assert len(rows) == 1
    assert rows[0].dispute_id == 42
    assert rows[0].source == ReceiptSource.DISPUTE_BOT
    assert rows[0].sha256 == hashlib.sha256(_VALID_PDF).hexdigest()
    assert len(await ReceiptService(session).list_for_dispute(42)) == 1


@pytest.mark.asyncio
async def test_assert_can_add_rejects_duplicate_before_write(session):
    """The pre-save guard rejects a dup (and a cap overflow) so confirm_order
    never writes an orphaned file for a rejected upload."""
    order = await _make_order(session)
    svc = ReceiptService(session)
    await svc.add_receipt(order_id=order.id, file_path="a.pdf",
                          source=ReceiptSource.MERCHANT_API, sha256="dup")
    # Same sha → guard raises (no write would happen in confirm_order).
    with pytest.raises(DuplicateReceiptError):
        await svc.assert_can_add(order.id, "dup")
    # A fresh sha passes the guard.
    await svc.assert_can_add(order.id, "fresh")  # no raise


@pytest.mark.asyncio
async def test_assert_can_add_enforces_cap(session):
    order = await _make_order(session)
    svc = ReceiptService(session)
    for i in range(ReceiptService.MAX_PER_ORDER):
        await svc.add_receipt(order_id=order.id, file_path=f"r{i}.pdf",
                              source=ReceiptSource.SYSTEM, sha256=f"h{i}")
    with pytest.raises(ReceiptLimitReachedError):
        await svc.assert_can_add(order.id, "another")


# ── ReceiptService.upload() — the orchestrator, by moderation outcome ────────
# confirm_order is now a thin delegate; the lifecycle (validate → moderate →
# store → mirror+row+audit → effects) lives here. These pin the three outcome
# branches against a real session, mocking only the pipeline verdict + the
# external (Celery) boundary.


def _pipeline_returning(outcome, *, support_chat_id=0, reject_reason=""):
    """patch.object ctx that makes ReceiptModerationService.evaluate resolve to
    the given outcome, so we drive each branch without touching policy/settings."""
    from unittest.mock import AsyncMock, patch

    from app.modules.receipts.moderation import ModerationResult, ReceiptModerationService

    result = ModerationResult(outcome=outcome, support_chat_id=support_chat_id,
                              reject_reason=reject_reason)
    return patch.object(ReceiptModerationService, "evaluate",
                        AsyncMock(return_value=result))


@pytest.mark.asyncio
async def test_upload_auto_approve_stores_receipt_and_fires_effects(session, tmp_path, monkeypatch):
    from unittest.mock import MagicMock, patch

    from app.modules.receipts.moderation import ModerationOutcome

    monkeypatch.setattr("app.modules.receipts.storage.settings.UPLOAD_DIR",
                        str(tmp_path), raising=False)
    order = await _make_order(session, status=OrderStatus.PENDING)
    merchant = await session.get(Merchant, order.merchant_id)

    fire = MagicMock()
    send_task = MagicMock()
    with _pipeline_returning(ModerationOutcome.AUTO_APPROVE), \
            patch("app.modules.receipts.effects.fire_receipt_approved", fire), \
            patch("app.workers.celery_app.celery_app.send_task", send_task):
        out = await ReceiptService(session).upload(
            order=order, merchant=merchant, content=b"approve-me", filename="r.pdf",
        )

    # Stored (mod_status NONE — trader-visible) + mirror moved to RECEIPT_UPLOADED.
    assert out.status == OrderStatus.RECEIPT_UPLOADED
    rows = await ReceiptService(session).list_for_order(order.id)
    assert len(rows) == 1 and rows[0].moderation_status == ModerationStatus.NONE
    # Approved effects fired once, threading the new receipt id for cascade.
    fire.assert_called_once()
    assert fire.call_args.args[0] == order.id
    assert fire.call_args.kwargs["receipt_id"] == rows[0].id
    # Merchant webhook fired on the PENDING → RECEIPT_UPLOADED transition.
    names = [c.args[0] for c in send_task.call_args_list]
    assert "app.workers.tasks.callbacks.send_order_callback" in names


@pytest.mark.asyncio
async def test_upload_escalate_human_sends_support_bot_no_approved_effects(session, tmp_path, monkeypatch):
    from unittest.mock import MagicMock, patch

    from app.modules.receipts.moderation import ModerationOutcome
    from app.modules.receipts.repository import ReceiptModerationRepository

    monkeypatch.setattr("app.modules.receipts.storage.settings.UPLOAD_DIR",
                        str(tmp_path), raising=False)
    order = await _make_order(session, status=OrderStatus.PENDING)
    merchant = await session.get(Merchant, order.merchant_id)

    fire = MagicMock()
    send_task = MagicMock()
    with _pipeline_returning(ModerationOutcome.ESCALATE_HUMAN, support_chat_id=777), \
            patch("app.modules.receipts.effects.fire_receipt_approved", fire), \
            patch("app.workers.celery_app.celery_app.send_task", send_task):
        out = await ReceiptService(session).upload(
            order=order, merchant=merchant, content=b"review-me", filename="r.pdf",
        )

    # Receipt persisted but held PENDING (hidden from the trader) until review.
    rows = await ReceiptService(session).list_for_order(order.id)
    assert len(rows) == 1 and rows[0].moderation_status == ModerationStatus.PENDING
    assert out.status == OrderStatus.RECEIPT_UPLOADED
    # A REAL moderation review row was opened with the resolved support chat.
    review = await ReceiptModerationRepository(session).get_latest_for_order(order.id)
    assert review is not None and review.chat_id == 777
    # Support-bot enqueued with the review-row id; NO approved effects.
    sent = [c for c in send_task.call_args_list
            if c.args and "send_receipt_to_support_bot" in c.args[0]]
    assert sent and sent[0].kwargs["args"] == [order.id, review.id]
    fire.assert_not_called()


@pytest.mark.asyncio
async def test_upload_rejects_terminal_order(session, tmp_path, monkeypatch):
    from app.core.exceptions import ConflictException
    from app.modules.receipts.moderation import ModerationOutcome

    monkeypatch.setattr("app.modules.receipts.storage.settings.UPLOAD_DIR",
                        str(tmp_path), raising=False)
    # SUCCESS is terminal — outside RECEIPT_UPLOADABLE_STATUSES.
    order = await _make_order(session, status=OrderStatus.SUCCESS)
    merchant = await session.get(Merchant, order.merchant_id)

    with _pipeline_returning(ModerationOutcome.AUTO_APPROVE):
        with pytest.raises(ConflictException):
            await ReceiptService(session).upload(
                order=order, merchant=merchant, content=b"x", filename="r.pdf",
            )
    # Nothing stored for a terminal order.
    assert await ReceiptRepository(session).count_for_order(order.id) == 0
