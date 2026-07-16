"""Trader + admin payout endpoints (role-guarded per endpoint).

Trader: see the ACL-allowed pool, claim, upload partial receipts, list own.
Admin: list payouts, manage terminals (CRUD + balance top-up + trader ACL),
moderate receipts, force cancel / complete.
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.common.enums.payouts import PayoutStatus
from app.core.exceptions import NotFoundException
from app.modules.payouts.models import Payout, PayoutReceipt, PayoutTerminal
from app.modules.payouts.schemas.admin import (
    AdminPayoutListResponse,
    AdminPayoutReceiptItem,
    AdminPayoutRejectRequest,
    AdminPayoutResponse,
    AdminPayoutTerminalConfig,
    AdminPayoutTerminalCreate,
    AdminPayoutTerminalCreated,
    AdminPayoutTerminalResponse,
    AdminPayoutTopupRequest,
)
from app.modules.payouts.schemas.trader import TraderPayoutPoolItem, TraderPayoutResponse
from app.modules.payouts.service import PayoutService
from app.modules.payouts.terminal_service import PayoutTerminalService
from app.modules.users.models import User
from app.modules.users.permissions import get_current_user, require_admin, require_trader

router = APIRouter()


# ── builders ──────────────────────────────────────────────────────────


def _option_name(p: Payout) -> Optional[str]:
    opt = p.payment_option
    return opt.name if opt else None


def _pool_item(p: Payout) -> TraderPayoutPoolItem:
    return TraderPayoutPoolItem(
        id=str(p.uuid), amount=float(p.amount), currency=p.currency, payment_method=p.payment_method,
        amount_usdt=(float(p.amount_usdt) if p.amount_usdt is not None else None),
        payment_option_name=_option_name(p), created_at=p.created_at, expires_at=p.expires_at,
    )


def _trader_resp(p: Payout) -> TraderPayoutResponse:
    return TraderPayoutResponse(
        id=str(p.uuid), status=p.status, amount=float(p.amount), currency=p.currency,
        payment_method=p.payment_method, payment_option_name=_option_name(p),
        amount_usdt=(float(p.amount_usdt) if p.amount_usdt is not None else None),
        trader_fee_usdt=(float(p.trader_fee_usdt) if p.trader_fee_usdt is not None else None),
        req_holder=p.req_holder, req_number=p.req_number, req_extra=p.req_extra,
        client_user_id=p.client_user_id, receipt_file=p.receipt_file,
        receipt_uploaded_at=p.receipt_uploaded_at, claimed_at=p.claimed_at,
        claim_expires_at=p.claim_expires_at, trader_hold_until=p.trader_hold_until,
        created_at=p.created_at, expires_at=p.expires_at, completed_at=p.completed_at,
    )


def _admin_resp(p: Payout) -> AdminPayoutResponse:
    return AdminPayoutResponse(
        id=str(p.uuid), external_id=p.external_id, payout_terminal_id=p.payout_terminal_id,
        trader_id=p.trader_id, client_user_id=p.client_user_id, payment_method=p.payment_method,
        payment_option_name=_option_name(p), amount=float(p.amount), currency=p.currency,
        amount_usdt=(float(p.amount_usdt) if p.amount_usdt is not None else None),
        exchange_rate=(float(p.exchange_rate) if p.exchange_rate is not None else None),
        merchant_fee_usdt=(float(p.merchant_fee_usdt) if p.merchant_fee_usdt is not None else None),
        trader_fee_usdt=(float(p.trader_fee_usdt) if p.trader_fee_usdt is not None else None),
        req_holder=p.req_holder, req_number=p.req_number, req_extra=p.req_extra,
        status=p.status, rejection_reason=p.rejection_reason, receipt_file=p.receipt_file,
        receipt_uploaded_at=p.receipt_uploaded_at, claimed_at=p.claimed_at,
        claim_expires_at=p.claim_expires_at, trader_hold_until=p.trader_hold_until,
        hold_released_at=p.hold_released_at, expires_at=p.expires_at, created_at=p.created_at,
        completed_at=p.completed_at, canceled_at=p.canceled_at,
    )


def _receipt_item(r: PayoutReceipt) -> AdminPayoutReceiptItem:
    return AdminPayoutReceiptItem(
        id=r.id, payout_id=r.payout_id, trader_id=r.trader_id, amount=float(r.amount),
        file=r.file, status=r.status.value, rejection_reason=r.rejection_reason,
        created_at=r.created_at, moderated_at=r.moderated_at,
    )


async def _terminal_resp(svc: PayoutTerminalService, t: PayoutTerminal, *, secret: Optional[str] = None):
    bal = await svc.get_balances(t.id)
    trader_ids = await svc.trader_ids(t.id)
    base = dict(
        id=t.id, owner_user_id=t.user_id, name=t.name, status=t.status.value, currency=t.currency,
        api_key=t.api_key, rate_config_id=t.rate_config_id,
        commission_percent=float(t.commission_percent or 0), ttl_minutes=t.ttl_minutes,
        receipts_to_close=t.receipts_to_close,
        min_amount=(float(t.min_amount) if t.min_amount is not None else None),
        max_amount=(float(t.max_amount) if t.max_amount is not None else None),
        webhook_url=t.webhook_url, work_usdt=float(bal["work_usdt"]), escrow_usdt=float(bal["escrow_usdt"]),
        trader_ids=trader_ids, created_at=t.created_at,
    )
    if secret is not None:
        return AdminPayoutTerminalCreated(**base, api_secret=secret)
    return AdminPayoutTerminalResponse(**base)


# ── trader ──────────────────────────────────────────────────────────────


@router.get("/pool", response_model=List[TraderPayoutPoolItem], dependencies=[Depends(require_trader)],
            summary="Claimable payouts (ACL-filtered pool)")
async def payout_pool(
    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db),
):
    payouts = await PayoutService(session).list_pool_for_trader(current_user, skip=skip, limit=limit)
    return [_pool_item(p) for p in payouts]


@router.get("/mine", response_model=List[TraderPayoutResponse], dependencies=[Depends(require_trader)],
            summary="Trader's claimed/completed payouts")
async def my_payouts(
    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db),
):
    payouts = await PayoutService(session).list_trader_payouts(current_user.id, skip=skip, limit=limit)
    return [_trader_resp(p) for p in payouts]


@router.post("/{uuid}/claim", response_model=TraderPayoutResponse, dependencies=[Depends(require_trader)],
             summary="Claim a payout from the pool")
async def claim_payout(
    uuid: str, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db),
):
    payout = await PayoutService(session).claim_payout(uuid, current_user)
    return _trader_resp(payout)


@router.post("/{uuid}/receipt", response_model=TraderPayoutResponse, dependencies=[Depends(require_trader)],
             summary="Upload a partial-payment receipt")
async def upload_receipt(
    uuid: str,
    amount: float = Form(..., gt=0),
    attachment: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    payout = await PayoutService(session).add_receipt(uuid, current_user, attachment, amount)
    return _trader_resp(payout)


# ── admin: payouts ────────────────────────────────────────────────────────


@router.get("", response_model=AdminPayoutListResponse, dependencies=[Depends(require_admin)],
            summary="List payouts (paginated)")
async def list_payouts(
    status_filter: Optional[PayoutStatus] = Query(None, alias="status"),
    terminal_id: Optional[int] = None, trader_id: Optional[int] = None,
    skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
):
    repo = PayoutService(session).repository
    payouts = await repo.list_admin(
        status=status_filter, terminal_id=terminal_id, trader_id=trader_id, skip=skip, limit=limit,
    )
    total = await repo.count_admin(status=status_filter, terminal_id=terminal_id, trader_id=trader_id)
    return AdminPayoutListResponse(items=[_admin_resp(p) for p in payouts], total=total)


@router.post("/{uuid}/cancel", response_model=AdminPayoutResponse, dependencies=[Depends(require_admin)],
             summary="Force-cancel a payout (refund)")
async def admin_cancel(
    uuid: str, body: Optional[AdminPayoutRejectRequest] = None,
    current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db),
):
    payout = await PayoutService(session).admin_cancel(uuid, current_user.id, reason=(body.reason if body else None))
    return _admin_resp(payout)


@router.post("/{uuid}/complete", response_model=AdminPayoutResponse, dependencies=[Depends(require_admin)],
             summary="Force-complete a payout (settle)")
async def admin_complete(
    uuid: str, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db),
):
    payout = await PayoutService(session).admin_complete(uuid, current_user.id)
    return _admin_resp(payout)


@router.get("/{uuid}/receipts", response_model=List[AdminPayoutReceiptItem], dependencies=[Depends(require_admin)],
            summary="List a payout's receipts")
async def list_receipts(uuid: str, session: AsyncSession = Depends(get_db)):
    svc = PayoutService(session)
    payout = await svc.repository.get_by_uuid(uuid)
    if not payout:
        raise NotFoundException("Payout not found")
    return [_receipt_item(r) for r in await svc.repository.list_receipts(payout.id)]


@router.post("/receipts/{receipt_id}/approve", response_model=AdminPayoutResponse,
             dependencies=[Depends(require_admin)], summary="Approve a receipt")
async def approve_receipt(
    receipt_id: int, current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db),
):
    payout = await PayoutService(session).admin_approve_receipt(receipt_id, current_user.id)
    return _admin_resp(payout)


@router.post("/receipts/{receipt_id}/reject", response_model=AdminPayoutResponse,
             dependencies=[Depends(require_admin)], summary="Reject a receipt")
async def reject_receipt(
    receipt_id: int, body: AdminPayoutRejectRequest,
    current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db),
):
    payout = await PayoutService(session).admin_reject_receipt(receipt_id, current_user.id, body.reason)
    return _admin_resp(payout)


# ── admin: terminals ──────────────────────────────────────────────────────


@router.get("/terminals", response_model=List[AdminPayoutTerminalResponse], dependencies=[Depends(require_admin)],
            summary="List payout terminals")
async def list_terminals(
    owner_id: Optional[int] = None, skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
):
    svc = PayoutTerminalService(session)
    terminals = await svc.list_terminals(owner_id=owner_id, skip=skip, limit=limit)
    return [await _terminal_resp(svc, t) for t in terminals]


@router.get("/terminals/{terminal_id}", response_model=AdminPayoutTerminalResponse,
            dependencies=[Depends(require_admin)], summary="Get a payout terminal")
async def get_terminal(terminal_id: int, session: AsyncSession = Depends(get_db)):
    svc = PayoutTerminalService(session)
    terminal = await svc.get_terminal(terminal_id)
    return await _terminal_resp(svc, terminal)


@router.post("/terminals", response_model=AdminPayoutTerminalCreated, status_code=status.HTTP_201_CREATED,
             dependencies=[Depends(require_admin)], summary="Create a payout terminal")
async def create_terminal(
    data: AdminPayoutTerminalCreate,
    current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db),
):
    svc = PayoutTerminalService(session)
    terminal, _api_key, api_secret = await svc.create_terminal(data, admin_user_id=current_user.id)
    return await _terminal_resp(svc, terminal, secret=api_secret)


@router.patch("/terminals/{terminal_id}", response_model=AdminPayoutTerminalResponse,
              dependencies=[Depends(require_admin)], summary="Update a payout terminal")
async def update_terminal(
    terminal_id: int, config: AdminPayoutTerminalConfig,
    current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db),
):
    svc = PayoutTerminalService(session)
    terminal = await svc.update_terminal(terminal_id, config, admin_user_id=current_user.id)
    return await _terminal_resp(svc, terminal)


@router.post("/terminals/{terminal_id}/topup", response_model=AdminPayoutTerminalResponse,
             dependencies=[Depends(require_admin)], summary="Top up a terminal's balance")
async def topup_terminal(
    terminal_id: int, body: AdminPayoutTopupRequest,
    current_user: User = Depends(get_current_user), session: AsyncSession = Depends(get_db),
):
    svc = PayoutTerminalService(session)
    await svc.topup(terminal_id, body.amount, admin_user_id=current_user.id)
    terminal = await svc.get_terminal(terminal_id)
    return await _terminal_resp(svc, terminal)
