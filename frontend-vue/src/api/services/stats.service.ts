import api from '@/api/client'

export interface AdminStats {
  turnover_usdt: number
  profit_usdt: number
  requests_rub: number
  requests_rub_24h: number
  payout_pct: number
  payin_requests_total: number
  payin_requests_24h: number
  orders_total: number
  orders_success: number
  orders_active: number
  conversion_pct: number
  merchants_online_24h: number
  traders_online_24h: number
  pending_withdrawals: number
  active_disputes: number
}

export interface VolumeDistribution {
  method: string
  lt_1000: number
  from_1000: number
  from_5000: number
  from_8000: number
  from_10000: number
  from_20000: number
}

export interface ActiveStats {
  orders_active: number
  active_disputes: number
  pending_withdrawals: number
  requisites_traffic_active: number
}

export interface TimeseriesPoint {
  ts: number
  turnover_usdt: number
  profit_usdt: number
  orders: number
  // Cascade-only slice — orders routed through a provider (won attempt).
  provider_turnover_usdt: number
  provider_profit_usdt: number
  provider_orders: number
}

export interface AdminTimeseries {
  granularity: '3hour' | 'hour' | 'day' | 'week' | 'month'
  points: TimeseriesPoint[]
}

export interface ActivitySegment {
  from_amount: number
  to_amount: number
  requisites: number
}

export interface ActivityBar {
  min: number
  max: number
  segments: ActivitySegment[]
}

export interface ActivityBucket {
  ts: number
  trader_count: number
  bars: ActivityBar[]
}

export interface AdminActivity {
  granularity: 'minute' | '3hour' | 'hour' | 'day' | 'week' | 'month'
  currency: string
  available_currencies: string[]
  buckets: ActivityBucket[]
}

export interface MerchantStatsRow {
  merchant_id: number
  merchant_login: string | null
  requests: number
  orders_created: number
  orders_success: number
  conversion_pct: number
  payout_pct: number
  // 13 RUB volume sums of successful orders, aligned with CHECK_SIZE_LABELS.
  check_size_buckets: number[]
}

export interface MerchantTimeseriesPoint {
  ts: number
  requests: number
  created: number
  success: number
}

export interface MerchantTimeseries {
  granularity: '3hour' | 'hour' | 'day' | 'week' | 'month'
  points: MerchantTimeseriesPoint[]
}

// Labels for check_size_buckets — must stay aligned with the backend's
// merchant_metrics.check_size_bounds() (13 buckets, left-incl / right-excl).
export const CHECK_SIZE_LABELS = [
  '<1000', '1000–2000', '2000–3000', '3000–4000', '4000–5000', '5000–6000',
  '6000–7000', '7000–8000', '8000–9000', '9000–10000', '10000–15000',
  '15000–20000', '20000+',
] as const

export interface CheckerTotals {
  total: number
  success: number
  failed: number
  clean: number
  suspicious: number
  suspicious_rate: number      // 0..1
  spent_usdt: number
  refunded_usdt: number
  net_usdt: number
  avg_price_usdt: number
}

export interface CheckerProviderRow {
  provider_id: number | null
  checker: string
  adapter_type: string | null
  total: number
  success: number
  failed: number
  manual: number
  auto: number
  clean: number
  suspicious: number
  suspicious_rate: number
  spent_usdt: number
  refunded_usdt: number
  net_usdt: number
  avg_price_usdt: number
}

export interface CheckerTraderRow {
  trader_user_id: number
  username: string | null
  checks: number
  spent_usdt: number
  suspicious_rate: number
}

export interface CheckerStats {
  totals: CheckerTotals
  providers: CheckerProviderRow[]
  top_traders: CheckerTraderRow[]
}

export interface CheckerTimeseriesPoint {
  ts: number
  checks: number
  spent_usdt: number
}

export interface CheckerTimeseries {
  granularity: '3hour' | 'hour' | 'day' | 'week' | 'month'
  points: CheckerTimeseriesPoint[]
}

export const statsService = {
  getAdminStats: (params?: { date_from?: number; date_to?: number }) =>
    api.get<AdminStats>('/api/v1/stats/admin', { params }),

  getMerchantStats: (params?: { date_from?: number; date_to?: number }) =>
    api.get<MerchantStatsRow[]>('/api/v1/stats/admin/merchants', { params }),

  getMerchantTimeseries: (params: {
    merchant_id: number
    date_from?: number
    date_to?: number
    granularity?: '3hour' | 'hour' | 'day' | 'week' | 'month'
  }) => api.get<MerchantTimeseries>('/api/v1/stats/admin/merchants/timeseries', { params }),

  getOrderRequests: (params: { page: number; limit: number }) =>
    api.get<{ items: any[]; total: number }>('/api/v1/stats/admin/order-requests', { params }),

  getVolumeDistribution: () =>
    api.get<VolumeDistribution[]>('/api/v1/stats/admin/volume-distribution'),

  getAdminTimeseries: (params?: {
    date_from?: number
    date_to?: number
    granularity?: '3hour' | 'hour' | 'day' | 'week' | 'month'
  }) => api.get<AdminTimeseries>('/api/v1/stats/admin/timeseries', { params }),

  getActivityTimeseries: (params?: {
    date_from?: number
    date_to?: number
    granularity?: 'minute' | '3hour' | 'hour' | 'day' | 'week' | 'month'
    currency?: string
  }) => api.get<AdminActivity>('/api/v1/stats/admin/activity', { params }),

  getActiveStats: () =>
    api.get<ActiveStats>('/api/v1/stats/me/active'),

  getCheckerStats: (params?: { date_from?: number; date_to?: number }) =>
    api.get<CheckerStats>('/api/v1/stats/admin/checkers', { params }),

  getCheckerTimeseries: (params?: {
    date_from?: number
    date_to?: number
    granularity?: '3hour' | 'hour' | 'day' | 'week' | 'month'
  }) => api.get<CheckerTimeseries>('/api/v1/stats/admin/checkers/timeseries', { params }),
}
