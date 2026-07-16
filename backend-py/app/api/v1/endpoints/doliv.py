"""Долив (requisite refill) endpoints — trader requester + доливщик.

A trader requests a долив against their own payin order's requisite; a доливщик
(a trader in the platform-settings executor list) claims it from the pool and
executes it (confirms the real transfer). All run on the trader JWT — the
executor gate is the settings list, enforced in ``DolivService``.
"""
import mimetypes
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_db
from app.common.enums.payouts import PayoutStatus
from app.modules.doliv.schemas.admin import (
    AdminDolivListResponse,
    AdminDolivResponse,
    AdminDolivStatusUpdate,
)
from app.modules.doliv.schemas.trader import (
    DolivCreateRequest,
    DolivExecutorAccess,
    DolivLimits,
    DolivResponse,
)
from app.modules.doliv.service import DolivService
from app.modules.payouts.models import Payout
from app.modules.users.models import User
from app.modules.users.permissions import get_current_user, require_admin, require_trader

router = APIRouter()


def _popt(popts: dict, p) -> tuple:
    return popts.get(p.payment_option_id) or (None, None)


def _doliv_resp(p, popts: dict, *, mask_req: bool = False, hide_reward: bool = False) -> DolivResponse:
    name, logo = _popt(popts, p)
    resp = DolivResponse.from_payout(p, logo_url=logo, payment_option_name=name)
    if mask_req:
        resp.req_number = None
        resp.req_holder = None
        resp.req_extra = None
    if hide_reward:
        # Executor reward is the доливщик's earnings — never exposed to the
        # requester. A regular trader sees only what THEY pay (the price); the
        # доливщик's cut stays on the executor/pool views.
        resp.executor_reward_usdt = None
    return resp


def _admin_doliv(p: Payout, umap: dict, popts: dict) -> AdminDolivResponse:
    name, logo = _popt(popts, p)
    return AdminDolivResponse.from_payout(
        p,
        requester_username=umap.get(p.requester_trader_id),
        executor_username=umap.get(p.trader_id),
        logo_url=logo,
        payment_option_name=name,
    )


# ── requester ──────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=DolivResponse,
    dependencies=[Depends(require_trader)],
    summary="Request a долив against your payin order's requisite",
)
async def create_doliv(
    data: DolivCreateRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    svc = DolivService(session)
    payout = await svc.create(current_user, data.requisite_id, data.amount)
    popts = await svc.resolve_payment_options([payout])
    return _doliv_resp(payout, popts, hide_reward=True)


@router.get(
    "/mine",
    response_model=List[DolivResponse],
    dependencies=[Depends(require_trader)],
    summary="Доливы I requested",
)
async def list_my_doliv(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    svc = DolivService(session)
    rows = await svc.list_mine(current_user, skip=skip, limit=limit)
    popts = await svc.resolve_payment_options(rows)
    return [_doliv_resp(p, popts, hide_reward=True) for p in rows]


@router.post(
    "/{uuid}/cancel",
    response_model=DolivResponse,
    dependencies=[Depends(require_trader)],
    summary="Cancel an unclaimed (CREATED) долив (requester) → refund",
)
async def cancel_doliv(
    uuid: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    svc = DolivService(session)
    payout = await svc.cancel(current_user, uuid)
    popts = await svc.resolve_payment_options([payout])
    return _doliv_resp(payout, popts, hide_reward=True)


# ── доливщик ───────────────────────────────────────────────────────────

@router.get(
    "/pool",
    response_model=List[DolivResponse],
    dependencies=[Depends(require_trader)],
    summary="Claimable доливы (доливщик only)",
)
async def list_doliv_pool(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    svc = DolivService(session)
    rows = await svc.list_pool(current_user, skip=skip, limit=limit)
    popts = await svc.resolve_payment_options(rows)
    return [_doliv_resp(p, popts, mask_req=True) for p in rows]


@router.get(
    "/access",
    response_model=DolivExecutorAccess,
    dependencies=[Depends(require_trader)],
    summary="Am I a доливщик? (drives the «Долив» tab visibility)",
)
async def doliv_access(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    is_executor = await DolivService(session).is_executor(current_user.id)
    return DolivExecutorAccess(is_executor=is_executor)


@router.get(
    "/config",
    response_model=DolivLimits,
    dependencies=[Depends(require_trader)],
    summary="Долив config (amount range + price percent) for the create modal",
)
async def doliv_config(
    session: AsyncSession = Depends(get_db),
):
    cfg_min, cfg_max, price_pct = await DolivService(session).amount_bounds()
    return DolivLimits(
        min_amount=float(cfg_min), max_amount=float(cfg_max), price_percent=float(price_pct),
    )


@router.get(
    "/all",
    response_model=AdminDolivListResponse,
    dependencies=[Depends(require_admin)],
    summary="Admin «Доливы» page: filtered, paginated list with trader usernames",
)
async def list_all_doliv(
    status: Optional[PayoutStatus] = Query(None),
    requester_trader_id: Optional[int] = Query(None),
    executor_trader_id: Optional[int] = Query(None),
    created_from: Optional[datetime] = Query(None),
    created_to: Optional[datetime] = Query(None),
    search: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    session: AsyncSession = Depends(get_db),
):
    svc = DolivService(session)
    filters = dict(
        status=status, requester_trader_id=requester_trader_id,
        executor_trader_id=executor_trader_id, created_from=created_from,
        created_to=created_to, search=search,
    )
    rows = await svc.list_all(**filters, skip=skip, limit=limit)
    total = await svc.count_all(**filters)
    umap = await svc.resolve_usernames(rows)
    popts = await svc.resolve_payment_options(rows)
    return AdminDolivListResponse(items=[_admin_doliv(p, umap, popts) for p in rows], total=total)


@router.post(
    "/{uuid}/admin/status",
    response_model=AdminDolivResponse,
    dependencies=[Depends(require_admin)],
    summary="Admin: move a долив to ANY status (state machine applies the money)",
)
async def admin_change_doliv_status(
    uuid: str,
    body: AdminDolivStatusUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    svc = DolivService(session)
    payout = await svc.admin_change_status(uuid, body.status, current_user)
    umap = await svc.resolve_usernames([payout])
    popts = await svc.resolve_payment_options([payout])
    return _admin_doliv(payout, umap, popts)


@router.get(
    "/executor",
    response_model=List[DolivResponse],
    dependencies=[Depends(require_trader)],
    summary="Доливщик's «Долив» tab: open pool + my taken доливы (with result)",
)
async def list_executor_doliv(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    svc = DolivService(session)
    rows = await svc.list_for_executor(current_user, skip=skip, limit=limit)
    popts = await svc.resolve_payment_options(rows)
    # Mask the requisite of pool доливы the доливщик hasn't taken yet (item 7).
    return [_doliv_resp(p, popts, mask_req=p.trader_id != current_user.id) for p in rows]


@router.post(
    "/{uuid}/claim",
    response_model=DolivResponse,
    dependencies=[Depends(require_trader)],
    summary="Claim a долив from the pool (доливщик)",
)
async def claim_doliv(
    uuid: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    svc = DolivService(session)
    payout = await svc.claim(current_user, uuid)
    popts = await svc.resolve_payment_options([payout])
    return _doliv_resp(payout, popts)


@router.post(
    "/{uuid}/execute",
    response_model=DolivResponse,
    dependencies=[Depends(require_trader)],
    summary="Confirm the real transfer (attach receipt) → settle the долив (доливщик)",
)
async def execute_doliv(
    uuid: str,
    attachment: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    # Receipt is mandatory — the service validates + stores it (the proof the
    # requester sees + gets in the bot).
    content = await attachment.read()
    svc = DolivService(session)
    payout = await svc.execute(
        current_user, uuid, receipt_content=content, receipt_filename=attachment.filename,
    )
    popts = await svc.resolve_payment_options([payout])
    return _doliv_resp(payout, popts)


@router.get(
    "/{uuid}/receipt",
    response_class=FileResponse,
    summary="Download the долив receipt (requester, доливщик, or admin)",
)
async def get_doliv_receipt(
    uuid: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    payout = await DolivService(session).get_with_receipt_access(uuid, current_user)
    media_type, _ = mimetypes.guess_type(payout.receipt_file)
    return FileResponse(
        path=payout.receipt_file,
        media_type=media_type or "application/octet-stream",
        filename=os.path.basename(payout.receipt_file),
    )
