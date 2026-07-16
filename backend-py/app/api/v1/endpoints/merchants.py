import mimetypes
import os
from datetime import datetime
from io import BytesIO
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse

from app.api.dependencies import get_service
from app.api.multipart import clean_urls, read_attachments
from app.common.enums.balances import BalanceType
from app.common.enums.disputes import DisputeReason
from app.common.enums.finances import Currency, WithdrawalStatus
from app.common.enums.orders import OrderSource, OrderStatus
from app.common.enums.payments import PaymentMethod
from app.common.enums.receipts import ReceiptUploader
from app.common.types import UnixTimestamp
from app.core.exceptions import NotFoundException
from app.modules.disputes.schemas import (
    DisputeCreate,
    DisputeMerchantResponse,
)
from app.modules.disputes.service import DisputeService
from app.modules.receipts.schemas import ReceiptItem
from app.modules.finance.schemas import (
    WithdrawalRequestCreate,
    WithdrawalRequestResponse,
)
from app.modules.finance.service import FinanceService
from app.modules.merchants.schemas import (
    ApiKeyResetResponse,
    MerchantAdminResponse,
    MerchantAdminUpdate,
    MerchantCreateRequest,
    MerchantCreateResponse,
    MerchantFullProfileResponse,
    MerchantListItem,
    MerchantSettingsUpdate,
    PaymentMethodInfo,
)
from app.modules.merchants.service import MerchantService, normalize_telegram_users
from app.modules.orders.schemas import (
    MerchantOrderResponse,
    MerchantPayinCreate,
    OrderResponse,
    PaginatedOrderResponse,
    RequisiteInfo,
    MerchantRequisiteInfo,
)
from app.modules.orders.service import ExportService, OrderService
from app.modules.payments.service import PaymentOptionService
from app.common.enums.payouts import PayoutStatus
from app.modules.payouts.schemas.merchant import (
    MerchantPayoutListItem,
    PaginatedMerchantPayoutResponse,
)
from app.modules.payouts.service import PayoutService
from app.modules.stats.schemas import MerchantStatsResponse
from app.modules.stats.service import StatsService
from app.modules.users.models import User
from app.modules.users.permissions import require_admin, require_merchant
from app.modules.users.service import UserService
from app.workers.tasks.callbacks import send_order_callback

router = APIRouter()

MID = Query(None, alias="merchant_id", description="Merchant ID (for multi-merchant users)")

_XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)
# OOM backstop for the export query; the endpoint logs when it truncates.
_EXPORT_ROW_CAP = 100_000


# ════════════════════════════════════════════════════════════
# Admin endpoints
# ════════════════════════════════════════════════════════════


@router.get(
    "/",
    response_model=List[MerchantAdminResponse],
    dependencies=[Depends(require_admin)],
    summary="List all merchants (admin)",
)
async def list_merchants(
    skip: int = 0,
    limit: int = 100,
    search: Optional[str] = Query(None, description="Search by merchant ID or user login"),
    status: Optional[str] = Query(None, description="Filter by merchant status"),
    is_active: Optional[bool] = Query(None, description="Filter by active status (enabled/test)"),
    payment_method: Optional[str] = Query(None, description="Filter by configured payment method"),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
):
    return await merchant_service.list_all(
        skip=skip,
        limit=limit,
        search=search,
        status=status,
        is_active=is_active,
        payment_method=payment_method,
    )


# ════════════════════════════════════════════════════════════
# Merchant endpoints
# ════════════════════════════════════════════════════════════


# ── Merchants list / create ────────────────────────────────


@router.get(
    "/me/merchants",
    response_model=List[MerchantListItem],
    summary="List own merchant profiles",
)
async def list_my_merchants(
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
):
    merchants = await merchant_service.list_user_merchants(current_user.id)
    return [
        MerchantListItem(
            id=m.id,
            name=m.name,
            status=m.status,
            currency=m.currency,
            api_key_masked=MerchantService.mask_api_key(m.api_key),
            webhook_url=m.webhook_url,
            order_ttl_seconds=m.order_ttl_seconds,
        )
        for m in merchants
    ]


@router.post(
    "/me/merchants",
    response_model=MerchantCreateResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new merchant profile",
)
async def create_my_merchant(
    data: MerchantCreateRequest,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
):
    merchant, api_key, api_secret = await merchant_service.create_merchant(
        current_user.id, name=data.name,
    )
    return MerchantCreateResponse(
        id=merchant.id,
        name=merchant.name,
        status=merchant.status,
        currency=merchant.currency,
        api_key=api_key,
        api_secret=api_secret,
    )


# ── Profile ────────────────────────────────────────────────


@router.get(
    "/me/profile",
    response_model=MerchantFullProfileResponse,
    summary="Get merchant profile",
)
async def get_my_profile(
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    finance_service: FinanceService = Depends(get_service(FinanceService)),
    payment_service: PaymentOptionService = Depends(get_service(PaymentOptionService)),
):
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)

    # Single source of truth: every credit/debit path in `FinanceService`
    # (payin/payout confirm, cancel, refund, dispute payout, withdrawal
    # freeze/release) writes to the *merchant* balance in USDT and ignores
    # `users.use_shared_balance` for merchants. Reading the user balance here
    # — as the old `if use_shared_balance` branch did — would therefore always
    # return 0 for production data, because no code path ever credits it.
    # We follow the same convention as `/finances/my-balances`: read from
    # `merchant_id`.
    balance_currency = Currency.USDT
    work = await finance_service.get_or_create_merchant_balance(merchant, balance_currency, BalanceType.WORK)
    escrow = await finance_service.get_or_create_merchant_balance(merchant, balance_currency, BalanceType.ESCROW)
    escrow_amount = float(escrow.amount)

    active_options = await payment_service.get_active_options()
    methods_map: dict[PaymentMethod, list] = {}
    for method in PaymentMethod:
        methods_map[method] = []
    for option in active_options:
        for method_str in option.supported_methods:
            pm = PaymentMethod.__members__.get(method_str) or _safe_payment_method(method_str)
            if pm is not None:
                methods_map.setdefault(pm, []).append(option)

    merchant_fees = merchant.fees or {}
    payment_methods_info = []
    for method, options in methods_map.items():
        has_options = bool(options)
        has_fee_configured = method.value in merchant_fees
        if has_options or has_fee_configured:
            fee = float(merchant_fees.get(method.value, 0.0))
            payment_methods_info.append(
                PaymentMethodInfo(method=method, fee_percentage=fee)
            )

    return MerchantFullProfileResponse(
        id=merchant.id,
        name=merchant.name,
        status=merchant.status,
        currency=merchant.currency,
        webhook_url=merchant.webhook_url,
        api_key_masked=MerchantService.mask_api_key(merchant.api_key),
        balance_work=float(work.amount),
        balance_escrow=escrow_amount,
        order_ttl_seconds=merchant.order_ttl_seconds,
        requisite_search_timeout_ms=merchant.requisite_search_timeout_ms,
        fees=merchant_fees,
        withdrawal_fee_fixed=float(merchant.withdrawal_fee_fixed),
        payment_methods=payment_methods_info,
        telegram_user_ids=normalize_telegram_users(merchant.telegram_user_ids),
    )


def _safe_payment_method(value: str) -> Optional[PaymentMethod]:
    try:
        return PaymentMethod(value)
    except ValueError:
        return None


# ── Settings ───────────────────────────────────────────────


@router.patch(
    "/me/settings",
    response_model=MerchantFullProfileResponse,
    summary="Update merchant settings",
)
async def update_my_settings(
    data: MerchantSettingsUpdate,
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    finance_service: FinanceService = Depends(get_service(FinanceService)),
    payment_service: PaymentOptionService = Depends(get_service(PaymentOptionService)),
):
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    await merchant_service.update_settings(
        merchant.id,
        data.model_dump(exclude_unset=True),
        user_id=current_user.id,
    )
    return await get_my_profile(
        merchant_id=merchant.id,
        current_user=current_user,
        merchant_service=merchant_service,
        finance_service=finance_service,
        payment_service=payment_service,
    )


@router.post(
    "/me/api-key/reset",
    response_model=ApiKeyResetResponse,
    summary="Reset own API key",
)
async def reset_my_api_key(
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
):
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    new_key, new_secret = await merchant_service.reset_api_key(
        merchant.id, admin_user_id=current_user.id,
    )
    return ApiKeyResetResponse(api_key=new_key, api_secret=new_secret)


# ── Stats ──────────────────────────────────────────────────


@router.get(
    "/me/stats",
    response_model=MerchantStatsResponse,
    summary="Get merchant statistics",
)
async def get_my_stats(
    date_from: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    date_to: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    stats_service: StatsService = Depends(get_service(StatsService)),
):
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    return await stats_service.get_cached_merchant_stats(
        merchant.id, date_from=date_from, date_to=date_to,
    )


# ── Orders ─────────────────────────────────────────────────


@router.get(
    "/me/orders",
    response_model=PaginatedOrderResponse,
    summary="List merchant orders (paginated)",
)
async def list_my_orders(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    order_status: Optional[OrderStatus] = Query(None, alias="status"),
    payment_method: Optional[PaymentMethod] = None,
    search: Optional[str] = Query(None, description="Search by UUID or external_id"),
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    order_service: OrderService = Depends(get_service(OrderService)),
):
    flt = dict(status=order_status, payment_method=payment_method, search=search)
    if merchant_id is None:
        items = await order_service.list_orders_for_user_merchants(
            current_user.id, **flt, skip=skip, limit=limit,
        )
        total = await order_service.count_orders_for_user_merchants(current_user.id, **flt)
        return {"items": items, "total": total}

    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    items = await order_service.list_merchant_orders(merchant.id, **flt, skip=skip, limit=limit)
    total = await order_service.count_merchant_orders(merchant.id, **flt)
    return {"items": items, "total": total}


# ── Payouts ────────────────────────────────────────────────


@router.get(
    "/me/payouts",
    response_model=PaginatedMerchantPayoutResponse,
    summary="List merchant payouts (paginated)",
)
async def list_my_payouts(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    payout_status: Optional[PayoutStatus] = Query(None, alias="status"),
    payment_method: Optional[PaymentMethod] = None,
    search: Optional[str] = Query(None, description="Search by UUID or external_id"),
    current_user: User = Depends(require_merchant),
    payout_service: PayoutService = Depends(get_service(PayoutService)),
):
    """Payouts across every payout terminal the merchant owns."""
    items, total = await payout_service.list_for_owner(
        current_user.id, status=payout_status, payment_method=payment_method,
        search=search, skip=skip, limit=limit,
    )
    return {"items": items, "total": total}


@router.post(
    "/me/payouts/{uuid}/cancel",
    response_model=MerchantPayoutListItem,
    summary="Cancel an unclaimed merchant payout",
)
async def cancel_my_payout(
    uuid: str,
    current_user: User = Depends(require_merchant),
    payout_service: PayoutService = Depends(get_service(PayoutService)),
):
    return await payout_service.cancel_for_owner(current_user.id, uuid)


@router.get(
    "/me/orders/export",
    summary="Export merchant orders to Excel (.xlsx)",
    response_class=StreamingResponse,
)
async def export_my_orders(
    date_from: datetime = Query(..., description="Period start (created_at >=), ISO-8601"),
    date_to: datetime = Query(..., description="Period end (created_at <=), ISO-8601"),
    current_user: User = Depends(require_merchant),
    export_service: ExportService = Depends(get_service(ExportService)),
):
    """Excel export of the merchant's orders for a period, across ALL of the
    owner's terminals (each row carries the terminal id). Selected by period
    only; no status/method filter.
    """
    content = await export_service.export_orders_xlsx(
        current_user.id, date_from=date_from, date_to=date_to, limit=_EXPORT_ROW_CAP,
    )
    filename = f"orders_{date_from:%Y-%m-%d}_{date_to:%Y-%m-%d}.xlsx"
    return StreamingResponse(
        BytesIO(content),
        media_type=_XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/me/orders/payin",
    response_model=MerchantOrderResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create payin order (merchant)",
)
async def create_my_payin_order(
    data: MerchantPayinCreate,
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    order_service: OrderService = Depends(get_service(OrderService)),
):
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    merchant_name = merchant.name
    order = await order_service.create_payin_order(merchant=merchant, data=data, source=OrderSource.WEB)

    req_info = None
    if order.requisite:
        opt = order.payment_option
        req_info = MerchantRequisiteInfo(
            bank_name=order.requisite.bank_name,
            account_number=order.requisite.account_number,
            account_holder=order.requisite.account_holder,
            payment_method=order.requisite.payment_method,
            currency=order.requisite.currency,
            payment_option_code=opt.code if opt else None,
            payment_option_name=opt.name if opt else None,
        )

    return MerchantOrderResponse(
        id=str(order.uuid),
        internalId=order.external_id,
        userId=order.client_user_id,
        merchant_name=merchant_name,
        amount=float(order.amount),
        amount_usdt=float(order.amount_usdt) if order.amount_usdt is not None else None,
        fee_usdt=float(order.fee_usdt) if order.fee_usdt is not None else None,
        exchange_rate=float(order.exchange_rate) if order.exchange_rate is not None else None,
        currency=order.currency,
        status=order.status,
        payment_url=order.payment_url,
        created_at=order.created_at,
        expires_at=order.date_end,
        requisite=req_info,
    )


@router.get(
    "/me/orders/{order_uuid}",
    response_model=OrderResponse,
    summary="Get order details",
)
async def get_my_order(
    order_uuid: str,
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    order_service: OrderService = Depends(get_service(OrderService)),
):
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    return await order_service.get_merchant_order(merchant=merchant, order_id=order_uuid)


@router.post(
    "/me/orders/{order_uuid}/callback/resend",
    status_code=status.HTTP_200_OK,
    summary="Resend callback for order",
)
async def resend_my_order_callback(
    order_uuid: str,
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    order_service: OrderService = Depends(get_service(OrderService)),
):
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    order = await order_service.get_merchant_order(merchant=merchant, order_id=order_uuid)
    send_order_callback.delay(order.id)
    return {"message": "Callback queued for resend"}


@router.get(
    "/me/orders/{order_uuid}/receipt",
    response_class=FileResponse,
    summary="Download receipt for merchant's order (cabinet)",
)
async def download_my_order_receipt(
    order_uuid: str,
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    order_service: OrderService = Depends(get_service(OrderService)),
):
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    order = await order_service.repository.get_by_uuid_and_merchant(order_uuid, merchant.id)
    if not order:
        raise NotFoundException(f"Order {order_uuid} not found")
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


# ── Withdrawals ────────────────────────────────────────────


@router.get(
    "/me/withdrawals",
    response_model=List[WithdrawalRequestResponse],
    summary="List merchant withdrawals",
)
async def list_my_withdrawals(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    withdrawal_status: Optional[WithdrawalStatus] = Query(None, alias="status"),
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    finance_service: FinanceService = Depends(get_service(FinanceService)),
):
    if merchant_id is None:
        return await finance_service.list_merchant_owner_withdrawals(
            current_user.id, status=withdrawal_status, skip=skip, limit=limit,
        )
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    return await finance_service.list_merchant_withdrawals(
        merchant.id, status=withdrawal_status, skip=skip, limit=limit,
    )


@router.post(
    "/me/withdrawals",
    response_model=WithdrawalRequestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create withdrawal request",
)
async def create_my_withdrawal(
    data: WithdrawalRequestCreate,
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    finance_service: FinanceService = Depends(get_service(FinanceService)),
):
    # Cross-terminal "sweep" withdrawals are disabled — a withdrawal always
    # charges a single terminal (the fee is withheld from the amount). The sweep
    # helper ``finance_service.create_merchant_sweep_withdrawal`` is retained but
    # no longer exposed.
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    return await finance_service.create_withdrawal_request(data, merchant=merchant)


# ── Disputes ───────────────────────────────────────────────


@router.get(
    "/me/disputes",
    response_model=List[DisputeMerchantResponse],
    summary="List merchant disputes",
)
async def list_my_disputes(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    dispute_service: DisputeService = Depends(get_service(DisputeService)),
):
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    return await dispute_service.list_merchant_disputes(
        merchant.id, skip=skip, limit=limit,
    )


@router.post(
    "/me/disputes",
    response_model=DisputeMerchantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Open a dispute",
)
async def open_my_dispute(
    order_uuid: str = Query(..., description="Order UUID to dispute"),
    reason: DisputeReason = Form(...),
    attachments: List[UploadFile] = File(default=[]),
    evidence_urls: List[str] = Form(default=[]),
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    dispute_service: DisputeService = Depends(get_service(DisputeService)),
):
    """Open a dispute on one of the merchant's own orders (multipart/form-data).
    Optional evidence: upload files (``attachments``) and/or links we download
    (``evidence_urls``) — same format check + premoderation as the API."""
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    return await dispute_service.open_dispute_by_merchant(
        merchant=merchant,
        data=DisputeCreate(reason=reason, evidence_files=[]),
        order_id=order_uuid,
        attachments=await read_attachments(attachments),
        evidence_urls=clean_urls(evidence_urls),
    )


@router.get(
    "/me/disputes/{dispute_uuid}",
    response_model=DisputeMerchantResponse,
    summary="Get dispute details",
)
async def get_my_dispute(
    dispute_uuid: str,
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    dispute_service: DisputeService = Depends(get_service(DisputeService)),
):
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    return await dispute_service.get_merchant_dispute_detail(merchant, dispute_uuid)


@router.post(
    "/me/disputes/{dispute_uuid}/receipt",
    response_model=DisputeMerchantResponse,
    summary="Add a receipt to an open dispute",
)
async def add_my_dispute_receipt(
    dispute_uuid: str,
    attachment: UploadFile = File(..., description="Receipt file / photo (image or PDF)"),
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    dispute_service: DisputeService = Depends(get_service(DisputeService)),
):
    """Attach another receipt to one of the merchant's OPEN disputes (e.g. to
    fulfil a pdf/video proof request). Same premoderation path as the API."""
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    return await dispute_service.add_merchant_evidence(
        merchant, dispute_uuid, attachment, uploaded_by=ReceiptUploader.MERCHANT_WEB,
    )


@router.get(
    "/me/disputes/{dispute_uuid}/evidence",
    response_model=List[ReceiptItem],
    summary="List dispute evidence files (cabinet)",
)
async def list_my_dispute_evidence(
    dispute_uuid: str,
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    dispute_service: DisputeService = Depends(get_service(DisputeService)),
):
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    rows = await dispute_service.list_evidence_merchant(merchant, dispute_uuid)
    return [ReceiptItem.from_model(r) for r in rows]


@router.get(
    "/me/disputes/{dispute_uuid}/evidence/{receipt_uuid}",
    response_class=FileResponse,
    summary="Download a dispute evidence file (cabinet)",
)
async def download_my_dispute_evidence(
    dispute_uuid: str,
    receipt_uuid: str,
    merchant_id: Optional[int] = MID,
    current_user: User = Depends(require_merchant),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    dispute_service: DisputeService = Depends(get_service(DisputeService)),
):
    merchant = await merchant_service.get_merchant_for_user(current_user.id, merchant_id)
    receipt = await dispute_service.get_evidence_receipt_merchant(
        merchant, dispute_uuid, receipt_uuid
    )
    if not receipt.file_path or not os.path.exists(receipt.file_path):
        raise NotFoundException("Evidence file not found on disk")
    media_type, _ = mimetypes.guess_type(receipt.file_path)
    return FileResponse(
        path=receipt.file_path,
        media_type=media_type or "application/octet-stream",
        filename=os.path.basename(receipt.file_path),
    )


# ════════════════════════════════════════════════════════════
# Admin parameterized endpoints
# ════════════════════════════════════════════════════════════


@router.get(
    "/{merchant_id}",
    response_model=MerchantAdminResponse,
    dependencies=[Depends(require_admin)],
    summary="Get merchant by ID (admin)",
)
async def get_merchant_admin(
    merchant_id: int,
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
):
    return await merchant_service.get_by_id(merchant_id)


@router.get(
    "/{merchant_id}/stats",
    response_model=MerchantStatsResponse,
    dependencies=[Depends(require_admin)],
    summary="Get a merchant's stat cards (admin)",
)
async def get_merchant_stats_admin(
    merchant_id: int,
    date_from: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    date_to: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
    stats_service: StatsService = Depends(get_service(StatsService)),
):
    """Per-merchant metric cards for the admin merchant detail page.

    Without a date range → snapshot+delta (Redis-cached 30s, beat refresh
    every 600s). With date_from/date_to → live recompute. Mirrors the global
    dashboard's behaviour. ``get_by_id`` is called first so an unknown
    merchant_id returns 404 instead of zero-filled stats.
    """
    await merchant_service.get_by_id(merchant_id)  # 404 if not found
    return await stats_service.get_cached_merchant_stats(
        merchant_id, date_from=date_from, date_to=date_to,
    )


@router.patch(
    "/{merchant_id}",
    response_model=MerchantAdminResponse,
    dependencies=[Depends(require_admin)],
    summary="Update merchant settings (admin)",
)
async def update_merchant_admin(
    merchant_id: int,
    data: MerchantAdminUpdate,
    admin_user: User = Depends(require_admin),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
):
    return await merchant_service.update_settings(
        merchant_id,
        data.model_dump(exclude_unset=True),
        user_id=admin_user.id,
    )


@router.post(
    "/{merchant_id}/api-key/reset",
    response_model=ApiKeyResetResponse,
    status_code=status.HTTP_200_OK,
    summary="Reset Merchant API Key (admin)",
)
async def reset_api_key_admin(
    merchant_id: int,
    admin_user: User = Depends(require_admin),
    merchant_service: MerchantService = Depends(get_service(MerchantService)),
):
    new_api_key, new_api_secret = await merchant_service.reset_api_key(
        merchant_id, admin_user_id=admin_user.id,
    )
    return ApiKeyResetResponse(api_key=new_api_key, api_secret=new_api_secret)
