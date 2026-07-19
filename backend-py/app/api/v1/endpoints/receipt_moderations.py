"""Web endpoints for the receipt-premoderation history.

Two endpoints:
  * GET /api/v1/receipt-moderations
      Filterable list (pending / by-decision / per-merchant / by-date)
      with pagination. Used by the receipt moderation pages.
  * GET /api/v1/receipt-moderations/order/{order_id}
      All moderation rows for one order — used on the admin order detail
      page to render a per-cycle history.

Endpoints require admin or support. They do NOT include the receipt file blob —
the existing `/api/v1/orders/{id}/receipt` route serves that and bakes
the right auth/range handling.
"""
from datetime import datetime
from typing import Dict, Iterable, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_service
from app.common.enums.receipt_moderations import ModerationDecision
from app.core.exceptions import NotFoundException
from app.modules.orders.models import Order
from app.modules.orders.repository import OrderRepository
from app.modules.receipts.permissions import require_admin_or_support
from app.modules.receipts.schemas.admin import (
    AdminModerationDecisionRequest,
    AdminReceiptModerationItem,
    AdminReceiptModerationListResponse,
)
from app.modules.receipts.moderation import ReceiptModerationService
from app.modules.receipts.moderation.coordinator import apply_moderation_decision
from app.modules.users.models import User

router = APIRouter()


async def _fetch_trader_usernames(
    session: AsyncSession, orders: Iterable[Order]
) -> Dict[int, str]:
    """Batch-resolve trader_id → username for the given orders.

    Done in one IN-query to keep the listing endpoint O(1) on traders
    regardless of page size. Orders without a trader_id are skipped.
    """
    trader_ids = {o.trader_id for o in orders if o.trader_id is not None}
    if not trader_ids:
        return {}
    result = await session.execute(
        select(User.id, User.username).where(User.id.in_(trader_ids))
    )
    return {uid: uname for uid, uname in result.all()}


def _item_from_row(
    row,
    order: Order,
    trader_usernames: Dict[int, str],
) -> AdminReceiptModerationItem:
    merchant = getattr(order, "merchant", None)
    return AdminReceiptModerationItem(
        id=row.id,
        order_id=order.id,
        order_uuid=str(order.uuid),
        order_external_id=order.external_id,
        merchant_id=order.merchant_id,
        merchant_name=getattr(merchant, "name", None) if merchant else None,
        trader_id=order.trader_id,
        trader_username=trader_usernames.get(order.trader_id) if order.trader_id else None,
        moderation_status=order.moderation_status,
        has_receipt=bool(order.receipt_file),
        chat_id=row.chat_id,
        message_id=row.message_id,
        decision=row.decision,
        moderator_tg_id=row.moderator_tg_id,
        moderator_username=row.moderator_username,
        created_at=row.created_at,
        decided_at=row.decided_at,
    )


@router.get(
    "",
    response_model=AdminReceiptModerationListResponse,
    dependencies=[Depends(require_admin_or_support)],
    summary="List receipt-moderation rows (admin/support)",
)
async def list_moderations(
    decision: Optional[ModerationDecision] = Query(default=None),
    pending_only: bool = Query(default=False),
    merchant_id: Optional[int] = Query(default=None),
    date_from: Optional[datetime] = Query(default=None),
    date_to: Optional[datetime] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    moderation_service: ReceiptModerationService = Depends(
        get_service(ReceiptModerationService)
    ),
):
    rows, total = await moderation_service.repo.list_with_filters(
        decision=decision,
        pending_only=pending_only,
        merchant_id=merchant_id,
        date_from=date_from,
        date_to=date_to,
        skip=skip,
        limit=limit,
    )
    trader_usernames = await _fetch_trader_usernames(
        moderation_service.session, (order for _, order in rows)
    )
    items = [_item_from_row(row, order, trader_usernames) for row, order in rows]
    return AdminReceiptModerationListResponse(
        items=items, total=total, skip=skip, limit=limit
    )


@router.get(
    "/order/{order_id}",
    response_model=list[AdminReceiptModerationItem],
    dependencies=[Depends(require_admin_or_support)],
    summary="Moderation history for one order (admin/support)",
)
async def list_moderations_for_order(
    order_id: int,
    moderation_service: ReceiptModerationService = Depends(
        get_service(ReceiptModerationService)
    ),
):
    session = moderation_service.session
    order = await OrderRepository(session).get(order_id)
    if not order:
        raise NotFoundException(f"Order {order_id} not found")

    rows = await moderation_service.repo.list_for_order(order_id)
    trader_usernames = await _fetch_trader_usernames(session, [order])
    return [_item_from_row(r, order, trader_usernames) for r in rows]


@router.post(
    "/{order_id}/decide",
    response_model=AdminReceiptModerationItem,
    summary="Apply a moderation decision from the web UI",
)
async def decide_moderation(
    order_id: int,
    payload: AdminModerationDecisionRequest,
    current_user: User = Depends(require_admin_or_support),
    moderation_service: ReceiptModerationService = Depends(
        get_service(ReceiptModerationService)
    ),
):
    """Accept / Request-PDF / Request-Video on an order's pending check — the web
    equivalent of the support-bot inline buttons. Runs the SAME shared coordinator
    (first-wins decision + side effects), so a duplicate / racing click returns
    HTTP 400 conflict (``ModerationAlreadyDecidedError``). The acting user's
    login is recorded as the moderator (no Telegram id)."""
    session = moderation_service.session
    order = await OrderRepository(session).get(order_id)
    if not order:
        raise NotFoundException(f"Order {order_id} not found")

    row = await apply_moderation_decision(
        session=session,
        order=order,
        decision=payload.decision,
        moderator_username=current_user.username,
    )
    trader_usernames = await _fetch_trader_usernames(session, [order])
    return _item_from_row(row, order, trader_usernames)
