import mimetypes
import os
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.common.enums.orders import OrderStatus
from app.common.enums.receipt_checks import ReceiptCheckTrigger
from app.common.enums.receipt_moderations import is_receipt_visible_to_trader
from app.common.enums.users import UserRole
from app.core.exceptions import (
    ConflictException,
    ForbiddenException,
    NotFoundException,
    ValidationException,
)
from app.core.logging import get_logger
from app.modules.orders.schemas import (
    AdminOrderResponse,
    AdminOrderUpdate,
    FailOrderRequest,
    OrderDebugResponse,
    PaginatedAdminOrderResponse,
)
from app.modules.orders.schemas.trader import OrderTraderResponse, PaginatedTraderOrderResponse
from app.modules.orders.service import OrderService
from app.modules.receipts.schemas import ReceiptItem
from app.modules.receipts.service import ReceiptService
from app.modules.receipt_checks.schemas import (
    BulkLatestRequest,
    ManualCheckRequest,
    ReceiptCheckResponse,
)
from app.modules.receipt_checks.schemas.trader import (
    ReceiptCheckTraderResponse,
    TraderReceiptProviderResponse,
)
from app.modules.receipt_checks.service import ReceiptCheckService
from app.modules.traders.service import TraderService
from app.modules.users.models import User
from app.modules.users.permissions import get_current_user, require_admin, require_trader

router = APIRouter()


# --- Trader Endpoints ---

@router.get(
    "/my",
    response_model=PaginatedTraderOrderResponse,
    dependencies=[Depends(require_trader)],
    summary="List all orders for trader (paginated)",
)
async def list_my_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    status: Optional[OrderStatus] = None,
    payment_method: Optional[str] = Query(None),
    id_search: Optional[str] = Query(None, description="Search by order id/uuid/external_id"),
    amount_from: Optional[float] = Query(None, ge=0),
    amount_to: Optional[float] = Query(None, ge=0),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = OrderService(session)
    filters = dict(
        status=status,
        payment_method=payment_method,
        id_search=id_search,
        amount_from=amount_from,
        amount_to=amount_to,
    )
    items = await service.list_trader_orders(current_user.id, **filters, skip=skip, limit=limit)
    total = await service.count_trader_orders(current_user.id, **filters)
    return {"items": items, "total": total}


@router.get(
    "/my-active",
    response_model=List[OrderTraderResponse],
    dependencies=[Depends(require_trader)],
    summary="Get active orders for trader",
)
async def get_my_active_orders(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = OrderService(session)
    return await service.list_active_trader_orders(current_user.id)


@router.get(
    "/my-disputes",
    response_model=List[OrderTraderResponse],
    dependencies=[Depends(require_trader)],
    summary="List orders with disputes for trader",
)
async def list_my_disputed_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = OrderService(session)
    return await service.list_disputed_trader_orders(
        current_user.id, skip=skip, limit=limit,
    )


@router.post(
    "/{order_id}/success",
    response_model=OrderTraderResponse,
    dependencies=[Depends(require_trader)],
    summary="Mark order as successful",
)
async def mark_order_success(
    order_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = OrderService(session)
    return await service.complete_order(trader=current_user, order_id=order_id)


@router.post(
    "/{order_id}/settle",
    response_model=OrderTraderResponse,
    dependencies=[Depends(require_trader)],
    summary="Settle an already failed/canceled order to success (late payment)",
)
async def settle_failed_order(
    order_id: int,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = OrderService(session)
    return await service.trader_settle_failed_order(trader=current_user, order_id=order_id)


# --- Admin Endpoints ---

@router.get(
    "/",
    response_model=PaginatedAdminOrderResponse,
    dependencies=[Depends(require_admin)],
    summary="List all orders (Admin, paginated)",
)
async def list_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    merchant_id: Optional[int] = None,
    trader_id: Optional[int] = None,
    status: Optional[str] = None,
    search: Optional[str] = Query(None, description="Search by merchant/trader login"),
    id_search: Optional[str] = Query(None, description="Search by order id/uuid/external_id"),
    trader_login: Optional[str] = Query(None, description="Filter by trader login"),
    merchant_login: Optional[str] = Query(None, description="Filter by merchant login"),
    payment_method: Optional[str] = Query(None, description="Filter by payment method"),
    amount_from: Optional[float] = Query(None, ge=0, description="Minimum amount (RUB)"),
    amount_to: Optional[float] = Query(None, ge=0, description="Maximum amount (RUB)"),
    session: AsyncSession = Depends(get_db),
):
    service = OrderService(session)
    filters = dict(
        merchant_id=merchant_id,
        trader_id=trader_id,
        status=status,
        search=search,
        id_search=id_search,
        trader_login=trader_login,
        merchant_login=merchant_login,
        payment_method=payment_method,
        amount_from=amount_from,
        amount_to=amount_to,
    )
    items = await service.list_admin_orders(**filters, skip=skip, limit=limit)
    total = await service.count_admin_orders(**filters)
    return {"items": items, "total": total}


@router.patch(
    "/{order_id}",
    response_model=AdminOrderResponse,
    dependencies=[Depends(require_admin)],
    summary="Update order (Admin)",
)
async def update_order_admin(
    order_id: int,
    data: AdminOrderUpdate,
    admin_user: User = Depends(require_admin),
    session: AsyncSession = Depends(get_db),
):
    service = OrderService(session)
    return await service.admin_update_order(
        order_id=order_id, data=data, admin_user_id=admin_user.id,
    )


@router.get(
    "/debug/{identifier}",
    response_model=OrderDebugResponse,
    dependencies=[Depends(require_admin)],
    summary="Get full order debug info (Admin)",
    description="Returns a comprehensive object containing the order details, status history, merchant API logs, callback attempts, ledger entries, and disputes."
)
async def get_order_debug(
    identifier: str,
    session: AsyncSession = Depends(get_db),
):
    service = OrderService(session)
    return await service.get_order_debug_info(identifier)


@router.get(
    "/{order_id}/receipt",
    summary="Download order receipt",
    description=(
        "Admin: any order. "
        "Trader: only orders assigned to them. "
        "Merchant: not via this endpoint — use /api/merchant/v1/orders/{id}/receipt."
    ),
    response_class=FileResponse,
)
async def download_receipt(
    order_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = OrderService(session)

    if current_user.role == UserRole.ADMIN:
        order = await service.get_order_by_uuid(order_id)
    elif current_user.role == UserRole.TRADER:
        order = await service.get_trader_order_by_uuid(order_id, current_user.id)
    else:
        raise ForbiddenException("Access denied")

    if not order:
        raise NotFoundException(f"Order {order_id} not found")
    # Trader-side premoderation gate — single source of truth in
    # ``is_receipt_visible_to_trader``. Mirrors OrderTraderResponse so the
    # download path and the listing schema agree. Same 404 wording as the
    # missing-file branch so we don't leak the existence of a
    # pending-moderation receipt.
    if (
        current_user.role == UserRole.TRADER
        and not is_receipt_visible_to_trader(order.moderation_status)
    ):
        raise NotFoundException("No receipt uploaded for this order")
    if not order.receipt_file:
        raise NotFoundException("No receipt uploaded for this order")
    if not os.path.exists(order.receipt_file):
        raise NotFoundException("Receipt file not found on disk")

    media_type, _ = mimetypes.guess_type(order.receipt_file)
    filename = os.path.basename(order.receipt_file)
    return FileResponse(
        path=order.receipt_file,
        media_type=media_type or "application/octet-stream",
        filename=filename,
    )


async def _resolve_order_for_receipts(
    service: OrderService, order_id: str, current_user: User
):
    """Resolve the order for a receipt read + whether to apply the trader
    visibility gate. Admin → all receipts; trader → own order, visible only."""
    if current_user.role == UserRole.ADMIN:
        return await service.get_order_by_uuid(order_id), False
    if current_user.role == UserRole.TRADER:
        return await service.get_trader_order_by_uuid(order_id, current_user.id), True
    raise ForbiddenException("Access denied")


@router.get(
    "/{order_id}/receipts",
    response_model=List[ReceiptItem],
    summary="List all receipts for an order",
    description="Trader sees only premoderation-approved receipts; admin sees all.",
)
async def list_order_receipts(
    order_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = OrderService(session)
    order, visible_only = await _resolve_order_for_receipts(service, order_id, current_user)
    if not order:
        raise NotFoundException(f"Order {order_id} not found")

    receipts = await ReceiptService(session).list_for_order(
        order.id, visible_to_trader_only=visible_only
    )
    return [ReceiptItem.from_model(r) for r in receipts]


@router.get(
    "/{order_id}/receipts/{receipt_uuid}",
    summary="Download a specific receipt by its uuid",
    response_class=FileResponse,
)
async def download_order_receipt_by_uuid(
    order_id: str,
    receipt_uuid: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    service = OrderService(session)
    order, visible_only = await _resolve_order_for_receipts(service, order_id, current_user)
    if not order:
        raise NotFoundException(f"Order {order_id} not found")

    receipt = await ReceiptService(session).get_by_uuid(receipt_uuid)
    # Same 404 wording for not-found, wrong-order and pending-moderation so we
    # never leak the existence of a receipt the caller may not see.
    if not receipt or receipt.order_id != order.id:
        raise NotFoundException("Receipt not found")
    if visible_only and not is_receipt_visible_to_trader(receipt.moderation_status):
        raise NotFoundException("Receipt not found")
    if not receipt.file_path or not os.path.exists(receipt.file_path):
        raise NotFoundException("Receipt file not found on disk")

    media_type, _ = mimetypes.guess_type(receipt.file_path)
    return FileResponse(
        path=receipt.file_path,
        media_type=media_type or "application/octet-stream",
        filename=os.path.basename(receipt.file_path),
    )


# --- Receipt anti-fraud check ---


@router.post(
    "/receipt-check/bulk-latest",
    response_model=dict[str, Optional[ReceiptCheckTraderResponse]],
    dependencies=[Depends(require_trader)],
    summary="Bulk fetch latest receipt checks for many orders in one DB roundtrip",
    description=(
        "Returns `{order_uuid: ReceiptCheck | null}` for every UUID the "
        "trader actually owns. Replaces N parallel `GET /orders/{id}/"
        "receipt-check` calls that the orders list used to fire on mount "
        "and which saturated the API workers."
    ),
)
async def bulk_latest_receipt_checks(
    data: BulkLatestRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    order_service = OrderService(session)
    uuid_to_id = await order_service.repository.list_ids_for_trader_by_uuids(
        data.order_uuids, current_user.id,
    )
    if not uuid_to_id:
        return {u: None for u in data.order_uuids}

    check_service = ReceiptCheckService(session)
    checks_by_order = await check_service.latest_for_orders(list(uuid_to_id.values()))

    out: dict[str, Optional[ReceiptCheckTraderResponse]] = {}
    for u in data.order_uuids:
        oid = uuid_to_id.get(u)
        if oid is None:
            out[u] = None
            continue
        c = checks_by_order.get(oid)
        out[u] = ReceiptCheckTraderResponse.from_orm_check(c) if c else None
    return out


@router.get(
    "/{order_id}/receipt-check",
    response_model=Optional[ReceiptCheckResponse],
    summary="Get the latest receipt check for an order (admin: any; trader: own)",
)
async def get_receipt_check(
    order_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    order_service = OrderService(session)
    if current_user.role == UserRole.ADMIN:
        order = await order_service.get_order_by_uuid(order_id)
    elif current_user.role == UserRole.TRADER:
        order = await order_service.get_trader_order_by_uuid(order_id, current_user.id)
    else:
        raise ForbiddenException("Access denied")

    if not order:
        raise NotFoundException(f"Order {order_id} not found")

    check_service = ReceiptCheckService(session)
    latest = await check_service.latest_for_order(order.id)
    if latest is None:
        return None
    return ReceiptCheckResponse.from_orm_check(latest)


@router.post(
    "/{order_id}/receipt-check",
    response_model=ReceiptCheckTraderResponse,
    dependencies=[Depends(require_trader)],
    summary="Run a manual receipt verification (trader only).",
    description=(
        "Charges the trader's USDT WORK balance at the price of the SELECTED "
        "provider (`provider_id`); when omitted, the trader's default provider "
        "or the first active one is used. If the same file was already checked "
        "on this order, the previous verdict is replayed for free."
    ),
)
async def run_receipt_check(
    order_id: str,
    data: ManualCheckRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    order_service = OrderService(session)
    order = await order_service.get_trader_order_by_uuid(order_id, current_user.id)
    if not order:
        raise NotFoundException(f"Order {order_id} not found")
    if not data.confirm:
        raise ForbiddenException("Manual check requires explicit confirmation")

    check_service = ReceiptCheckService(session)
    trader = await TraderService(session).get_or_create_trader(current_user.id)
    try:
        provider = await check_service.resolve_provider_for_trader(
            trader, data.provider_id
        )
        if provider is None:
            raise ConflictException("No active receipt-check provider is configured")
        check = await check_service.run_check_for_order(
            order=order,
            trader_user=current_user,
            trigger=ReceiptCheckTrigger.MANUAL,
            provider=provider,
        )
    except ConflictException as exc:
        # All conflict scenarios on the receipt-check path are "service can't
        # do work right now" (no active provider, etc.). The trader doesn't
        # need to know which provider or why — surface a neutral message and
        # keep the original detail for Sentry/admin logs.
        _receipt_check_log.warning(
            "receipt-check manual conflict",
            order_id=order_id,
            trader_id=current_user.id,
            detail=exc.message,
        )
        raise ConflictException("Сервис проверки временно недоступен")
    except ValidationException as exc:
        msg = _sanitize_receipt_check_error(exc.message)
        if msg is None:
            _receipt_check_log.warning(
                "receipt-check manual validation hidden",
                order_id=order_id,
                trader_id=current_user.id,
                detail=exc.message,
            )
            raise ValidationException("Сервис проверки временно недоступен")
        raise ValidationException(msg)
    return ReceiptCheckResponse.from_orm_check(check)


_receipt_check_log = get_logger(__name__)

# Errors from `ReceiptCheckService` that ARE meaningful to the trader and
# should pass through (translated). Anything else collapses to the generic
# "service unavailable" message so the trader never sees provider names,
# disk paths, or other internals.
_TRADER_VISIBLE_RECEIPT_ERRORS: dict[str, str] = {
    "Order has no receipt to verify": "По ордеру нет чека для проверки",
    "Insufficient WORK balance to pay for a receipt check":
        "Недостаточно средств на балансе для проверки чека",
    "Selected receipt-check provider is not available":
        "Выбранный провайдер недоступен",
}


def _sanitize_receipt_check_error(detail: str) -> str | None:
    """Map the backend's technical exception text to a Russian trader-facing
    string. Returns None when the detail is not in the allow-list — caller
    should respond with the neutral "service unavailable" wording."""
    return _TRADER_VISIBLE_RECEIPT_ERRORS.get(detail)


@router.get(
    "/receipt-check/providers",
    response_model=List[TraderReceiptProviderResponse],
    dependencies=[Depends(require_trader)],
    summary="List active receipt-check providers the trader can choose from",
    description=(
        "Returns the ACTIVE providers (id, name, price) the trader may select "
        "in the check-receipt modal and set as their profile default. An empty "
        "list means no provider is available (service unavailable)."
    ),
)
async def list_receipt_check_providers(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    check_service = ReceiptCheckService(session)
    providers = await check_service.list_active_providers()
    return [TraderReceiptProviderResponse.from_orm_provider(p) for p in providers]
