from datetime import datetime
from typing import List, Optional

from app.modules.base.schemas import BaseResponseSchema


class AdminStatsResponse(BaseResponseSchema):
    turnover_usdt: float
    profit_usdt: float
    requests_rub: float
    requests_rub_24h: float
    payout_count: int
    payout_pct: float
    payin_requests_total: int
    payin_requests_24h: int
    orders_total: int
    orders_success: int
    orders_active: int
    conversion_pct: float
    merchants_online_24h: int
    traders_online_24h: int
    pending_withdrawals: int
    active_disputes: int


class MerchantStatsResponse(BaseResponseSchema):
    turnover_usdt: float
    fee_usdt: float
    orders_total: int
    orders_success: int
    orders_active: int
    orders_failed: int
    conversion_pct: float
    pending_withdrawals: int
    active_disputes: int
    requests_rub: float = 0.0
    payin_requests_total: int = 0
    payout_pct: float = 0.0


class ActiveStatsResponse(BaseResponseSchema):
    orders_active: int
    active_disputes: int
    pending_withdrawals: int
    requisites_traffic_active: int = 0


class OrderRequestLogResponse(BaseResponseSchema):
    # Carries the ClickHouse-backed snapshot's request_id (a UUID string), not a
    # numeric row id — the source table has no autoincrement id.
    id: Optional[str] = None
    merchant_id: Optional[int]
    merchant_login: Optional[str]
    amount_rub: Optional[float]
    method: Optional[str]
    success: bool
    response_time_ms: Optional[int]
    created_at: datetime


class PaginatedOrderRequestsResponse(BaseResponseSchema):
    items: List[OrderRequestLogResponse]
    total: int


class MethodVolumeDistributionResponse(BaseResponseSchema):
    method: str
    lt_1000: float
    from_1000: float
    from_5000: float
    from_8000: float
    from_10000: float
    from_20000: float


class TimeseriesPointResponse(BaseResponseSchema):
    """One bucket of the admin dashboard turnover / profit chart."""
    ts: int  # Unix seconds (UTC)
    turnover_usdt: float
    profit_usdt: float
    orders: int
    provider_turnover_usdt: float = 0.0
    provider_profit_usdt: float = 0.0
    provider_orders: int = 0


class AdminTimeseriesResponse(BaseResponseSchema):
    granularity: str  # 'hour' | 'day' | 'week' | 'month'
    points: List[TimeseriesPointResponse]


class MerchantStatsRowResponse(BaseResponseSchema):
    """One per-merchant row of the «Мерчанты» tab table over the selected period.

    ``check_size_buckets`` is 13 RUB volume sums of SUCCESSFUL orders, aligned
    with ``merchant_metrics.check_size_bounds()`` (``<1000 … 20000+``).
    """
    merchant_id: int
    merchant_login: Optional[str] = None
    requests: int              # payin-create requests, incl. non-created
    orders_created: int
    orders_success: int
    conversion_pct: float      # успешные / созданные
    payout_pct: float          # созданные / запросы («Выдача»)
    check_size_buckets: List[float]


class MerchantTimeseriesPointResponse(BaseResponseSchema):
    """One time bucket of a single merchant's funnel chart."""
    ts: int  # Unix seconds (UTC)
    requests: int
    created: int
    success: int


class MerchantTimeseriesResponse(BaseResponseSchema):
    granularity: str  # 'hour' | '3hour' | 'day' | 'week' | 'month'
    points: List[MerchantTimeseriesPointResponse]


class AdminActivitySegment(BaseResponseSchema):
    """A slice of a bar where the covering-requisite count is constant — drives
    the hover tooltip's «N реквизитов на этом уровне»."""
    from_amount: float
    to_amount: float
    requisites: int


class AdminActivityBar(BaseResponseSchema):
    """One merged limit range (union of overlapping requisite [min,max]) at a
    time bucket — a floating bar on the «Активность» chart."""
    min: float
    max: float
    segments: List[AdminActivitySegment]


class AdminActivityBucket(BaseResponseSchema):
    """One time bucket: the merged bars + the unique-trader count that colors
    them (0–15 red / 15–30 yellow / 30–45 green / 45+ solid green)."""
    ts: int  # Unix seconds (UTC)
    trader_count: int
    bars: List[AdminActivityBar]


class AdminActivityResponse(BaseResponseSchema):
    granularity: str  # 'minute' | 'hour' | '3hour' | 'day' | 'week' | 'month'
    currency: str
    available_currencies: List[str]
    buckets: List[AdminActivityBucket]


class CheckerTotals(BaseResponseSchema):
    total: int
    success: int
    failed: int
    clean: int
    suspicious: int
    suspicious_rate: float     # 0..1 fraction
    spent_usdt: float
    refunded_usdt: float
    net_usdt: float
    avg_price_usdt: float


class CheckerProviderRow(BaseResponseSchema):
    provider_id: Optional[int]
    checker: str
    adapter_type: Optional[str]
    total: int
    success: int
    failed: int
    manual: int
    auto: int
    clean: int
    suspicious: int
    suspicious_rate: float
    spent_usdt: float
    refunded_usdt: float
    net_usdt: float
    avg_price_usdt: float


class CheckerTraderRow(BaseResponseSchema):
    trader_user_id: int
    username: Optional[str]
    checks: int
    spent_usdt: float
    suspicious_rate: float


class CheckerStatsResponse(BaseResponseSchema):
    totals: CheckerTotals
    providers: List[CheckerProviderRow]
    top_traders: List[CheckerTraderRow]


class CheckerTimeseriesPoint(BaseResponseSchema):
    ts: int              # Unix seconds (UTC)
    checks: int
    spent_usdt: float


class CheckerTimeseriesResponse(BaseResponseSchema):
    granularity: str     # '3hour' | 'hour' | 'day' | 'week' | 'month'
    points: List[CheckerTimeseriesPoint]
