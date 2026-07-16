"""Integration tests for the receipt READ endpoints (trader/admin) against a
real session. These exercise the security boundary directly: a trader must see
only their own order's premoderation-approved receipts; non-visible / wrong-
order / wrong-role all 404/403 without leaking existence.
"""
import itertools
import os
import uuid as uuidlib
from types import SimpleNamespace

import pytest

from app.api.v1.endpoints.orders import (
    download_order_receipt_by_uuid,
    list_order_receipts,
)
from app.common.enums.finances import Currency
from app.common.enums.orders import OrderStatus
from app.common.enums.payments import PaymentDirection, PaymentMethod
from app.common.enums.receipt_moderations import ModerationStatus
from app.common.enums.receipts import ReceiptSource
from app.common.enums.users import UserRole
from app.core.exceptions import ForbiddenException, NotFoundException
from app.modules.merchants.models import Merchant
from app.modules.orders.models import Order
from app.modules.receipts.service import ReceiptService

_seq = itertools.count(1)

TRADER = SimpleNamespace(role=UserRole.TRADER, id=7)
OTHER_TRADER = SimpleNamespace(role=UserRole.TRADER, id=999)
ADMIN = SimpleNamespace(role=UserRole.ADMIN, id=1)
MERCHANT = SimpleNamespace(role=UserRole.MERCHANT, id=3)


async def _order_with_receipts(session, *, trader_id=7):
    n = next(_seq)
    m = Merchant(user_id=n, api_key=f"k{n}", api_secret=f"s{n}", currency=Currency.RUB, fees={})
    session.add(m)
    await session.flush()
    order = Order(
        uuid=uuidlib.uuid4(), external_id=f"ext-{n}", merchant_id=m.id, trader_id=trader_id,
        direction=PaymentDirection.PAYIN, payment_method=PaymentMethod.SBP,
        amount=100, currency=Currency.RUB, status=OrderStatus.RECEIPT_UPLOADED,
        moderation_status=ModerationStatus.NONE,
    )
    session.add(order)
    await session.flush()
    svc = ReceiptService(session)
    approved = await svc.add_receipt(order_id=order.id, file_path="approved.pdf",
                                     source=ReceiptSource.MERCHANT_API, sha256="a",
                                     moderation_status=ModerationStatus.APPROVED)
    pending = await svc.add_receipt(order_id=order.id, file_path="pending.pdf",
                                    source=ReceiptSource.MERCHANT_API, sha256="p",
                                    moderation_status=ModerationStatus.PENDING)
    return order, approved, pending


@pytest.mark.asyncio
async def test_list_trader_sees_only_visible(session):
    order, _, _ = await _order_with_receipts(session, trader_id=TRADER.id)
    out = await list_order_receipts(str(order.uuid), current_user=TRADER, session=session)
    files = {r.filename for r in out}
    assert files == {"approved.pdf"}          # pending hidden from trader


@pytest.mark.asyncio
async def test_list_admin_sees_all(session):
    order, _, _ = await _order_with_receipts(session, trader_id=TRADER.id)
    out = await list_order_receipts(str(order.uuid), current_user=ADMIN, session=session)
    assert {r.filename for r in out} == {"approved.pdf", "pending.pdf"}


@pytest.mark.asyncio
async def test_list_other_trader_gets_404(session):
    order, _, _ = await _order_with_receipts(session, trader_id=TRADER.id)
    with pytest.raises(NotFoundException):
        await list_order_receipts(str(order.uuid), current_user=OTHER_TRADER, session=session)


@pytest.mark.asyncio
async def test_list_merchant_role_forbidden(session):
    order, _, _ = await _order_with_receipts(session, trader_id=TRADER.id)
    with pytest.raises(ForbiddenException):
        await list_order_receipts(str(order.uuid), current_user=MERCHANT, session=session)


@pytest.mark.asyncio
async def test_download_trader_pending_receipt_404(session):
    """Visibility gate: a trader cannot download a receipt still in
    premoderation — same 404 as not-found (no existence leak)."""
    order, _approved, pending = await _order_with_receipts(session, trader_id=TRADER.id)
    with pytest.raises(NotFoundException):
        await download_order_receipt_by_uuid(
            str(order.uuid), str(pending.uuid), current_user=TRADER, session=session,
        )


@pytest.mark.asyncio
async def test_download_receipt_of_other_order_404(session):
    order_a, app_a, _ = await _order_with_receipts(session, trader_id=TRADER.id)
    order_b, _, _ = await _order_with_receipts(session, trader_id=TRADER.id)
    # app_a belongs to order_a; requesting it under order_b must 404.
    with pytest.raises(NotFoundException):
        await download_order_receipt_by_uuid(
            str(order_b.uuid), str(app_a.uuid), current_user=TRADER, session=session,
        )


@pytest.mark.asyncio
async def test_download_admin_visible_returns_file(session, tmp_path):
    """Happy path: admin downloads an existing approved receipt → FileResponse."""
    order, approved, _ = await _order_with_receipts(session, trader_id=TRADER.id)
    real = tmp_path / "receipt.pdf"
    real.write_bytes(b"%PDF-1.4 fake")
    approved.file_path = str(real)
    await session.flush()

    resp = await download_order_receipt_by_uuid(
        str(order.uuid), str(approved.uuid), current_user=ADMIN, session=session,
    )
    assert os.path.basename(resp.path) == "receipt.pdf"
