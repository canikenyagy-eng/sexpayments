from typing import List
from fastapi import APIRouter, Depends, Query

from app.api.dependencies import get_service
from app.common.types import UnixTimestamp
from app.modules.stats.schemas import (
    ActiveStatsResponse,
    AdminActivityResponse,
    AdminStatsResponse,
    AdminTimeseriesResponse,
    CheckerStatsResponse,
    CheckerTimeseriesResponse,
    MerchantStatsRowResponse,
    MerchantTimeseriesResponse,
    MethodVolumeDistributionResponse,
    PaginatedOrderRequestsResponse,
)
from app.modules.stats.service import ReceiptCheckStatsService, StatsService
from app.modules.users.models import User
from app.modules.users.permissions import get_current_user, require_admin

router = APIRouter()


@router.get(
    "/admin",
    response_model=AdminStatsResponse,
    dependencies=[Depends(require_admin)],
)
async def get_admin_stats(
    date_from: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    date_to: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    stats_service: StatsService = Depends(get_service(StatsService)),
):
    result = await stats_service.get_cached_stats(
        date_from=date_from,
        date_to=date_to,
    )
    return result


@router.get(
    "/admin/order-requests",
    response_model=PaginatedOrderRequestsResponse,
    summary="List merchant API request logs specifically for order creation",
    dependencies=[Depends(require_admin)],
)
async def list_order_creation_requests(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    stats_service: StatsService = Depends(get_service(StatsService)),
):
    skip = (page - 1) * limit
    return await stats_service.list_order_creation_requests(skip=skip, limit=limit)


@router.get(
    "/admin/timeseries",
    response_model=AdminTimeseriesResponse,
    summary="Time-series of turnover & gross profit (USDT) for the admin dashboard chart",
    dependencies=[Depends(require_admin)],
)
async def get_admin_timeseries(
    date_from: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    date_to: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    granularity: str | None = Query(
        None,
        regex="^(3hour|hour|day|week|month)$",
        description="Bucket size; auto-picked from the range when omitted.",
    ),
    stats_service: StatsService = Depends(get_service(StatsService)),
):
    return await stats_service.get_admin_timeseries(
        date_from=date_from,
        date_to=date_to,
        granularity=granularity,
    )


@router.get(
    "/admin/activity",
    response_model=AdminActivityResponse,
    summary="Time-series of ready-requisite limit ranges for the «Активность» tab",
    dependencies=[Depends(require_admin)],
)
async def get_activity_timeseries(
    date_from: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    date_to: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    granularity: str | None = Query(
        None,
        regex="^(minute|3hour|hour|day|week|month)$",
        description="Bucket size; auto-picked from the range when omitted.",
    ),
    currency: str | None = Query(None, description="Fiat currency (default RUB)"),
    stats_service: StatsService = Depends(get_service(StatsService)),
):
    return await stats_service.get_activity_timeseries(
        date_from=date_from,
        date_to=date_to,
        granularity=granularity,
        currency=currency,
    )


@router.get(
    "/admin/merchants",
    response_model=List[MerchantStatsRowResponse],
    summary="Per-merchant funnel table over a period (requests → created → success + check-size buckets)",
    dependencies=[Depends(require_admin)],
)
async def get_merchant_stats(
    date_from: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    date_to: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    stats_service: StatsService = Depends(get_service(StatsService)),
):
    return await stats_service.get_merchant_stats(date_from=date_from, date_to=date_to)


@router.get(
    "/admin/merchants/timeseries",
    response_model=MerchantTimeseriesResponse,
    summary="Requests/created/success time-series for a single merchant",
    dependencies=[Depends(require_admin)],
)
async def get_merchant_timeseries(
    merchant_id: int = Query(..., ge=1),
    date_from: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    date_to: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    granularity: str | None = Query(
        None,
        regex="^(3hour|hour|day|week|month)$",
        description="Bucket size; auto-picked from the range when omitted.",
    ),
    stats_service: StatsService = Depends(get_service(StatsService)),
):
    return await stats_service.get_merchant_timeseries(
        merchant_id=merchant_id,
        date_from=date_from,
        date_to=date_to,
        granularity=granularity,
    )


@router.get(
    "/admin/volume-distribution",
    response_model=List[MethodVolumeDistributionResponse],
    summary="Get 24h order volume distribution by payment method",
    dependencies=[Depends(require_admin)],
)
async def get_volume_distribution(
    stats_service: StatsService = Depends(get_service(StatsService)),
):
    return await stats_service.get_volume_distribution_24h()


@router.get(
    "/admin/checkers",
    response_model=CheckerStatsResponse,
    summary="Receipt-check usage/cost/verdicts per provider for the «Чекер» tab",
    dependencies=[Depends(require_admin)],
)
async def get_checker_stats(
    date_from: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    date_to: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    service: ReceiptCheckStatsService = Depends(get_service(ReceiptCheckStatsService)),
):
    return await service.get_checker_stats(date_from=date_from, date_to=date_to)


@router.get(
    "/admin/checkers/timeseries",
    response_model=CheckerTimeseriesResponse,
    summary="Receipt-checks per bucket (count + spend) for the «Чекер» chart",
    dependencies=[Depends(require_admin)],
)
async def get_checker_timeseries(
    date_from: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    date_to: UnixTimestamp = Query(None, description="Unix timestamp (seconds)"),
    granularity: str | None = Query(
        None, regex="^(3hour|hour|day|week|month)$",
        description="Bucket size; auto-picked from the range when omitted.",
    ),
    service: ReceiptCheckStatsService = Depends(get_service(ReceiptCheckStatsService)),
):
    return await service.get_checker_timeseries(
        date_from=date_from, date_to=date_to, granularity=granularity,
    )


@router.get(
    "/me/active",
    response_model=ActiveStatsResponse,
    summary="Active counters (orders, disputes, withdrawals) for current user",
)
async def get_active_stats(
    current_user: User = Depends(get_current_user),
    stats_service: StatsService = Depends(get_service(StatsService)),
):
    return await stats_service.get_active_stats(current_user)
