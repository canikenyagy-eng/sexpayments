"""Admin API for cascade providers, groups, metrics and attempt logs."""
from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, status

from app.api.dependencies import get_service
from app.common.types import utcnow
from app.modules.cascading.integrations import registry as adapter_registry
from app.modules.cascading.repository import (
    CascadeOrderAttemptRepository,
    CascadeProviderMetricRepository,
)
from app.modules.cascading.schemas import (
    CascadeAdapterInfoResponse,
    CascadeGroupCreate,
    CascadeGroupResponse,
    CascadeGroupUpdate,
    CascadeMetricPoint,
    CascadeOrderAttemptResponse,
    CascadeProviderBalanceAdjust,
    CascadeProviderCreate,
    CascadeProviderMetricsResponse,
    CascadeProviderResponse,
    CascadeProviderTestIssueRequest,
    CascadeProviderUpdate,
    ProviderCallbackLogItem,
    ProviderRequestLogItem,
)
from app.modules.cascading.repository import (
    query_provider_callbacks,
    query_provider_requests,
)
from app.modules.cascading.service import CascadingService
from app.modules.users.permissions import require_admin

router = APIRouter()


# ─── adapter catalog ─────────────────────────────────────────


@router.get(
    "/adapters",
    response_model=List[CascadeAdapterInfoResponse],
    dependencies=[Depends(require_admin)],
    summary="List available provider adapters (with settings schema)",
)
async def list_adapters():
    """Return every registered ProviderAdapter with its display name and
    declarative settings schema. The admin UI uses this to populate the
    adapter dropdown and render an adapter-specific settings form.
    """
    return [
        type(adapter_registry.get(code)).describe()
        for code in adapter_registry.list_codes()
    ]


# ─── providers ───────────────────────────────────────────────


@router.get(
    "/providers",
    response_model=List[CascadeProviderResponse],
    dependencies=[Depends(require_admin)],
    summary="List cascade providers",
)
async def list_providers(
    skip: int = 0,
    limit: int = 100,
    is_active: Optional[bool] = None,
    search: Optional[str] = None,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    return await service.providers.list_all(
        skip=skip, limit=limit, is_active=is_active, search=search
    )


@router.get(
    "/providers/{provider_id}",
    response_model=CascadeProviderResponse,
    dependencies=[Depends(require_admin)],
    summary="Get cascade provider",
)
async def get_provider(
    provider_id: int,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    from app.core.exceptions import NotFoundException

    provider = await service.providers.get(provider_id)
    if not provider:
        raise NotFoundException(f"Cascade provider {provider_id} not found")
    return provider


@router.post(
    "/providers",
    response_model=CascadeProviderResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    summary="Create cascade provider (also bootstraps virtual user+trader)",
)
async def create_provider(
    data: CascadeProviderCreate,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    return await service.create_provider(data)


@router.patch(
    "/providers/{provider_id}",
    response_model=CascadeProviderResponse,
    dependencies=[Depends(require_admin)],
    summary="Update cascade provider",
)
async def update_provider(
    provider_id: int,
    data: CascadeProviderUpdate,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    return await service.update_provider(provider_id, data)


@router.delete(
    "/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    summary="Delete cascade provider (soft if attempt history exists)",
)
async def delete_provider(
    provider_id: int,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    await service.delete_provider(provider_id)


@router.post(
    "/providers/{provider_id}/balance",
    response_model=dict,
    dependencies=[Depends(require_admin)],
    summary="Adjust provider USDT balance (manual reconciliation)",
)
async def adjust_provider_balance(
    provider_id: int,
    data: CascadeProviderBalanceAdjust,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    new_balance = await service.adjust_provider_balance(
        provider_id,
        Decimal(str(data.delta_usdt)),
        reason=data.reason,
    )
    return {"new_balance_usdt": float(new_balance)}


@router.post(
    "/providers/{provider_id}/test-issue",
    response_model=dict,
    dependencies=[Depends(require_admin)],
    summary="Test a cascade provider live (issue requisite → auto-cancel)",
)
async def test_provider_issue(
    provider_id: int,
    data: CascadeProviderTestIssueRequest,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    """End-to-end smoke against the provider's live API.

    Synthesises an order_data payload (no DB writes) and calls the same
    ``adapter.issue_requisite`` the cascade scheduler would. On success
    the reservation is immediately rolled back via ``cancel_request`` so
    we don't park a trader on their side. The full envelope (request,
    timings, outcome, raw response, cancel status) is returned to the
    admin UI so operators can debug without grep-ing logs.
    """
    return await service.test_provider_issue(
        provider_id=provider_id,
        amount=Decimal(str(data.amount)),
        payment_method=data.payment_method,
        payment_option_code=data.payment_option_code,
        auto_cancel=data.auto_cancel,
    )


@router.get(
    "/providers/{provider_id}/upstream-balance",
    response_model=dict,
    dependencies=[Depends(require_admin)],
    summary="Query the provider's own balance endpoint (live, may be slow)",
)
async def upstream_provider_balance(
    provider_id: int,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    """Forwards to ``adapter.get_balance(provider)`` so admins can reconcile
    our locally-tracked virtual-trader balance with what the provider holds.

    Returns ``{"balance_usdt": null, "supported": false}`` when the adapter
    doesn't implement balance lookup, instead of raising — the page should
    just hide the cell.
    """
    from app.modules.cascading.integrations import registry as adapter_registry
    from app.modules.cascading.integrations.base import provider_request_type
    from app.core.exceptions import NotFoundException

    provider = await service.providers.get(provider_id)
    if not provider:
        raise NotFoundException(f"Cascade provider {provider_id} not found")

    adapter = adapter_registry.get(provider.adapter_type)
    with provider_request_type("balance"):
        balance = await adapter.get_balance(
            provider=provider, timeout_ms=provider.request_timeout_ms
        )
    return {
        "balance_usdt": float(balance) if balance is not None else None,
        "supported": balance is not None,
    }


@router.get(
    "/providers/{provider_id}/metrics",
    response_model=CascadeProviderMetricsResponse,
    dependencies=[Depends(require_admin)],
    summary="Provider metrics timeseries (hourly buckets)",
)
async def provider_metrics(
    provider_id: int,
    date_from: Optional[datetime] = None,
    date_to: Optional[datetime] = None,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    if not date_to:
        date_to = utcnow()
    if not date_from:
        date_from = date_to - timedelta(days=7)
    rows = await service.metrics.list_for_provider(
        provider_id, date_from=date_from, date_to=date_to
    )
    points: List[CascadeMetricPoint] = []
    totals_request = totals_success = totals_failure = 0
    totals_timeout = totals_cancel = 0
    totals_latency = 0
    totals_volume = Decimal("0")
    totals_profit = Decimal("0")
    for r in rows:
        success_rate = (
            (r.success_count / r.request_count) if r.request_count else 0.0
        )
        avg_latency = (
            (r.total_latency_ms / r.request_count) if r.request_count else 0.0
        )
        points.append(
            CascadeMetricPoint(
                bucket_at=r.bucket_at,
                request_count=r.request_count,
                success_count=r.success_count,
                failure_count=r.failure_count,
                timeout_count=r.timeout_count,
                cancel_count=r.cancel_count,
                avg_latency_ms=avg_latency,
                success_rate=success_rate,
                total_volume_usdt=r.total_volume_usdt,
                total_profit_usdt=r.total_profit_usdt,
            )
        )
        totals_request += r.request_count
        totals_success += r.success_count
        totals_failure += r.failure_count
        totals_timeout += r.timeout_count
        totals_cancel += r.cancel_count
        totals_latency += r.total_latency_ms
        totals_volume += r.total_volume_usdt
        totals_profit += r.total_profit_usdt

    totals = CascadeMetricPoint(
        bucket_at=date_to,
        request_count=totals_request,
        success_count=totals_success,
        failure_count=totals_failure,
        timeout_count=totals_timeout,
        cancel_count=totals_cancel,
        avg_latency_ms=(totals_latency / totals_request) if totals_request else 0.0,
        success_rate=(totals_success / totals_request) if totals_request else 0.0,
        total_volume_usdt=totals_volume,
        total_profit_usdt=totals_profit,
    )
    return CascadeProviderMetricsResponse(
        provider_id=provider_id,
        points=points,
        totals=totals,
    )


# ─── groups ──────────────────────────────────────────────────


@router.get(
    "/groups",
    response_model=List[CascadeGroupResponse],
    dependencies=[Depends(require_admin)],
    summary="List cascade groups",
)
async def list_groups(
    is_active: Optional[bool] = None,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    return await service.groups.list_all(is_active=is_active)


@router.get(
    "/groups/{group_id}",
    response_model=CascadeGroupResponse,
    dependencies=[Depends(require_admin)],
    summary="Get cascade group with providers and merchants",
)
async def get_group(
    group_id: int,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    from app.core.exceptions import NotFoundException

    group = await service.groups.get_with_relations(group_id)
    if not group:
        raise NotFoundException(f"Cascade group {group_id} not found")
    return group


@router.post(
    "/groups",
    response_model=CascadeGroupResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
    summary="Create cascade group",
)
async def create_group(
    data: CascadeGroupCreate,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    return await service.create_group(data)


@router.patch(
    "/groups/{group_id}",
    response_model=CascadeGroupResponse,
    dependencies=[Depends(require_admin)],
    summary="Update cascade group",
)
async def update_group(
    group_id: int,
    data: CascadeGroupUpdate,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    return await service.update_group(group_id, data)


@router.delete(
    "/groups/{group_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    summary="Delete cascade group",
)
async def delete_group(
    group_id: int,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    await service.delete_group(group_id)


@router.post(
    "/groups/{group_id}/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    summary="Attach provider to group",
)
async def attach_provider(
    group_id: int,
    provider_id: int,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    await service.attach_provider(group_id, provider_id)


@router.delete(
    "/groups/{group_id}/providers/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    summary="Detach provider from group",
)
async def detach_provider(
    group_id: int,
    provider_id: int,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    await service.detach_provider(group_id, provider_id)


@router.post(
    "/groups/{group_id}/merchants/{merchant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    summary="Attach merchant to group",
)
async def attach_merchant(
    group_id: int,
    merchant_id: int,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    await service.attach_merchant(group_id, merchant_id)


@router.delete(
    "/groups/{group_id}/merchants/{merchant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_admin)],
    summary="Detach merchant from group",
)
async def detach_merchant(
    group_id: int,
    merchant_id: int,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    await service.detach_merchant(group_id, merchant_id)


# ─── attempts ────────────────────────────────────────────────


@router.get(
    "/orders/{order_id}/attempts",
    response_model=List[CascadeOrderAttemptResponse],
    dependencies=[Depends(require_admin)],
    summary="List cascade attempts for an order (admin debug)",
)
async def order_attempts(
    order_id: int,
    service: CascadingService = Depends(get_service(CascadingService)),
):
    return await service.attempts.list_for_order(order_id)


# ─── inbound provider callbacks (admin debug) ────────────────


@router.get(
    "/callbacks",
    response_model=List[ProviderCallbackLogItem],
    dependencies=[Depends(require_admin)],
    summary="List inbound provider → us callbacks (ClickHouse)",
)
async def list_provider_callbacks(
    provider_code: Optional[str] = Query(None),
    order_id: Optional[int] = Query(None),
    external_order_id: Optional[str] = Query(None),
    signature_valid: Optional[bool] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
):
    return await query_provider_callbacks(
        limit=limit,
        offset=skip,
        provider_code=provider_code,
        order_id=order_id,
        external_order_id=external_order_id,
        signature_valid=signature_valid,
    )


# ─── provider request logs (ClickHouse) ──────────────────────


@router.get(
    "/provider-requests",
    response_model=List[ProviderRequestLogItem],
    dependencies=[Depends(require_admin)],
    summary="List outbound provider HTTP request logs (ClickHouse)",
)
async def list_provider_requests(
    provider_code: Optional[str] = Query(None),
    success: Optional[bool] = Query(None),
    order_id: Optional[str] = Query(None),
    request_id: Optional[str] = Query(None),
    request_type: Optional[str] = Query(None, description="payin / cancel / balance / check / upload / other"),
    skip: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=200),
):
    """Newest-first. Returns an empty list when ClickHouse is disabled."""
    return await query_provider_requests(
        limit=limit,
        offset=skip,
        provider_code=provider_code,
        success=success,
        order_id=order_id,
        request_id=request_id,
        request_type=request_type,
    )
