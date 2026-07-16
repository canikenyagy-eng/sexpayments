import api from '@/api/client'
import type {
  MerchantProfile,
  MerchantAdmin,
  MerchantFullProfile,
  MerchantSettingsUpdate,
  MerchantStats,
  MerchantPayinCreate,
  MerchantOrderResponse,
  MerchantListItem,
  MerchantCreateResponse,
  MerchantPayout,
  Order,
  Paginated,
  OrderStatus,
  PaymentMethod,
  WithdrawalRequest,
  WithdrawalStatus,
  DisputeResponse,
  DisputeReason,
  DisputeEvidenceItem,
} from '@/types'

function mid(merchantId?: number | null): Record<string, number> | undefined {
  return merchantId ? { merchant_id: merchantId } : undefined
}

export const merchantsService = {
  // ── API-key based (legacy) ──
  getProfile: () => api.get<MerchantProfile>('/api/merchant/v1/profile/me'),

  // ── Admin ──
  listAll: (params?: {
    skip?: number
    limit?: number
    search?: string
    status?: string
    is_active?: boolean
    payment_method?: PaymentMethod
  }) => api.get<MerchantAdmin[]>('/api/v1/merchants/', { params }),

  getByIdAdmin: (merchantId: number) =>
    api.get<MerchantAdmin>(`/api/v1/merchants/${merchantId}`),

  updateAdmin: (merchantId: number, data: Record<string, any>) =>
    api.patch<MerchantAdmin>(`/api/v1/merchants/${merchantId}`, data),

  resetApiKeyAdmin: (merchantId: number) =>
    api.post<{ api_key: string; api_secret: string }>(`/api/v1/merchants/${merchantId}/api-key/reset`),

  getStatsAdmin: (
    merchantId: number,
    params?: { date_from?: number; date_to?: number },
  ) => api.get<MerchantStats>(`/api/v1/merchants/${merchantId}/stats`, { params }),

  // ── Merchant list / create ──

  listMyMerchants: () =>
    api.get<MerchantListItem[]>('/api/v1/merchants/me/merchants'),

  createMerchant: (data: { name?: string }) =>
    api.post<MerchantCreateResponse>('/api/v1/merchants/me/merchants', data),

  // ── Merchant JWT endpoints (merchant_id optional for multi-merchant) ──

  getMyProfile: (merchantId?: number | null) =>
    api.get<MerchantFullProfile>('/api/v1/merchants/me/profile', { params: mid(merchantId) }),

  updateSettings: (data: MerchantSettingsUpdate, merchantId?: number | null) =>
    api.patch<MerchantFullProfile>('/api/v1/merchants/me/settings', data, { params: mid(merchantId) }),

  resetMyApiKey: (merchantId?: number | null) =>
    api.post<{ api_key: string; api_secret: string }>('/api/v1/merchants/me/api-key/reset', null, { params: mid(merchantId) }),

  getMyStats: (params?: { date_from?: number; date_to?: number; merchant_id?: number }) =>
    api.get<MerchantStats>('/api/v1/merchants/me/stats', { params }),

  listMyOrders: (params?: {
    skip?: number
    limit?: number
    status?: OrderStatus
    payment_method?: PaymentMethod
    search?: string
    merchant_id?: number
  }) => api.get<Paginated<Order>>('/api/v1/merchants/me/orders', { params }),

  // Excel export of orders for a period, across ALL of the owner's terminals.
  // Returns the raw .xlsx blob (responseType: 'blob').
  exportMyOrders: (params: { date_from: string; date_to: string }) =>
    api.get<Blob>('/api/v1/merchants/me/orders/export', { params, responseType: 'blob' }),

  createPayinOrder: (data: MerchantPayinCreate, merchantId?: number | null) =>
    api.post<MerchantOrderResponse>('/api/v1/merchants/me/orders/payin', data, { params: mid(merchantId) }),

  getMyOrder: (orderUuid: string, merchantId?: number | null) =>
    api.get<Order>(`/api/v1/merchants/me/orders/${orderUuid}`, { params: mid(merchantId) }),

  resendCallback: (orderUuid: string, merchantId?: number | null) =>
    api.post<{ message: string }>(`/api/v1/merchants/me/orders/${orderUuid}/callback/resend`, null, { params: mid(merchantId) }),

  // ── Payouts (across all the owner's payout terminals) ──
  listMyPayouts: (params?: {
    skip?: number
    limit?: number
    status?: string
    payment_method?: PaymentMethod
    search?: string
  }) => api.get<Paginated<MerchantPayout>>('/api/v1/merchants/me/payouts', { params }),

  cancelMyPayout: (uuid: string) =>
    api.post<MerchantPayout>(`/api/v1/merchants/me/payouts/${uuid}/cancel`),

  listMyWithdrawals: (params?: { skip?: number; limit?: number; status?: WithdrawalStatus; merchant_id?: number }) =>
    api.get<WithdrawalRequest[]>('/api/v1/merchants/me/withdrawals', { params }),

  createWithdrawal: (
    data: { amount: number; currency?: string; destination_address: string },
    options: { merchantId?: number | null } = {},
  ) => {
    // Always a single-terminal withdrawal; cross-terminal sweep is disabled.
    const params: Record<string, any> = {}
    if (options.merchantId) {
      params.merchant_id = options.merchantId
    }
    return api.post<WithdrawalRequest>(
      '/api/v1/merchants/me/withdrawals',
      data,
      { params },
    )
  },

  listMyDisputes: (params?: { skip?: number; limit?: number; merchant_id?: number }) =>
    api.get<DisputeResponse[]>('/api/v1/merchants/me/disputes', { params }),

  // multipart/form-data — files and links are optional.
  openDispute: (
    orderUuid: string,
    data: { reason: DisputeReason; files?: File[]; evidenceUrls?: string[] },
    merchantId?: number | null,
  ) => {
    const fd = new FormData()
    fd.append('reason', data.reason)
    for (const f of data.files ?? []) fd.append('attachments', f)
    for (const u of data.evidenceUrls ?? []) fd.append('evidence_urls', u)
    return api.post<DisputeResponse>('/api/v1/merchants/me/disputes', fd, {
      params: { order_uuid: orderUuid, ...mid(merchantId) },
    })
  },

  getMyDispute: (disputeUuid: string, merchantId?: number | null) =>
    api.get<DisputeResponse>(`/api/v1/merchants/me/disputes/${disputeUuid}`, { params: mid(merchantId) }),

  // Cabinet: list a dispute's evidence files. Bytes are fetched separately from
  // ``disputeEvidenceUrl`` via the authenticated downloader.
  listMyDisputeEvidence: (disputeUuid: string, merchantId?: number | null) =>
    api.get<DisputeEvidenceItem[]>(
      `/api/v1/merchants/me/disputes/${disputeUuid}/evidence`,
      { params: mid(merchantId) },
    ),

  disputeEvidenceUrl: (disputeUuid: string, receiptUuid: string) =>
    `/api/v1/merchants/me/disputes/${disputeUuid}/evidence/${receiptUuid}`,
}
