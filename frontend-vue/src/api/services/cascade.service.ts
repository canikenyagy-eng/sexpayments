import api from '@/api/client'
import type {
  CascadeAdapterInfo,
  CascadeGroup,
  CascadeGroupCreate,
  CascadeGroupUpdate,
  CascadeOrderAttempt,
  CascadeProvider,
  CascadeProviderCreate,
  CascadeProviderMetrics,
  CascadeProviderTestIssueResult,
  CascadeProviderUpdate,
  ProviderCallbackAttempt,
  ProviderRequestLog,
} from '@/types'

export const cascadeService = {
  // adapter catalog
  listAdapters: () =>
    api.get<CascadeAdapterInfo[]>('/api/v1/cascade/adapters'),

  // providers
  listProviders: (params?: {
    skip?: number
    limit?: number
    is_active?: boolean
    search?: string
  }) => api.get<CascadeProvider[]>('/api/v1/cascade/providers', { params }),

  getProvider: (id: number) =>
    api.get<CascadeProvider>(`/api/v1/cascade/providers/${id}`),

  createProvider: (data: CascadeProviderCreate) =>
    api.post<CascadeProvider>('/api/v1/cascade/providers', data),

  updateProvider: (id: number, data: CascadeProviderUpdate) =>
    api.patch<CascadeProvider>(`/api/v1/cascade/providers/${id}`, data),

  deleteProvider: (id: number) =>
    api.delete<void>(`/api/v1/cascade/providers/${id}`),

  adjustBalance: (id: number, delta_usdt: number, reason: string) =>
    api.post<{ new_balance_usdt: number }>(
      `/api/v1/cascade/providers/${id}/balance`,
      { delta_usdt, reason },
    ),

  upstreamBalance: (id: number) =>
    api.get<{ balance_usdt: number | null; supported: boolean }>(
      `/api/v1/cascade/providers/${id}/upstream-balance`,
    ),

  // Live-probe: hit the provider's real API end-to-end with a
  // synthesised order, then immediately roll the reservation back. No
  // DB writes on our side — useful for verifying signing, parsing and
  // network reachability without ordering through the cascade flow.
  testIssue: (
    id: number,
    body: {
      amount: number
      payment_method: string
      payment_option_code?: string | null
      auto_cancel?: boolean
    },
  ) =>
    api.post<CascadeProviderTestIssueResult>(
      `/api/v1/cascade/providers/${id}/test-issue`,
      body,
    ),

  providerMetrics: (id: number, params?: { date_from?: string; date_to?: string }) =>
    api.get<CascadeProviderMetrics>(`/api/v1/cascade/providers/${id}/metrics`, {
      params,
    }),

  // groups
  listGroups: (params?: { is_active?: boolean }) =>
    api.get<CascadeGroup[]>('/api/v1/cascade/groups', { params }),

  getGroup: (id: number) => api.get<CascadeGroup>(`/api/v1/cascade/groups/${id}`),

  createGroup: (data: CascadeGroupCreate) =>
    api.post<CascadeGroup>('/api/v1/cascade/groups', data),

  updateGroup: (id: number, data: CascadeGroupUpdate) =>
    api.patch<CascadeGroup>(`/api/v1/cascade/groups/${id}`, data),

  deleteGroup: (id: number) => api.delete<void>(`/api/v1/cascade/groups/${id}`),

  attachProvider: (groupId: number, providerId: number) =>
    api.post<void>(`/api/v1/cascade/groups/${groupId}/providers/${providerId}`),

  detachProvider: (groupId: number, providerId: number) =>
    api.delete<void>(`/api/v1/cascade/groups/${groupId}/providers/${providerId}`),

  attachMerchant: (groupId: number, merchantId: number) =>
    api.post<void>(`/api/v1/cascade/groups/${groupId}/merchants/${merchantId}`),

  detachMerchant: (groupId: number, merchantId: number) =>
    api.delete<void>(`/api/v1/cascade/groups/${groupId}/merchants/${merchantId}`),

  // attempts (debug)
  orderAttempts: (orderId: number) =>
    api.get<CascadeOrderAttempt[]>(`/api/v1/cascade/orders/${orderId}/attempts`),

  // inbound provider callbacks (admin Callbacks page → «Провайдеры» tab)
  listProviderCallbacks: (params?: {
    provider_code?: string
    order_id?: number
    external_order_id?: string
    signature_valid?: boolean
    skip?: number
    limit?: number
  }) =>
    api.get<ProviderCallbackAttempt[]>('/api/v1/cascade/callbacks', { params }),

  listProviderRequests: (params?: {
    provider_code?: string
    success?: boolean
    order_id?: string
    request_id?: string
    request_type?: string
    skip?: number
    limit?: number
  }) =>
    api.get<ProviderRequestLog[]>('/api/v1/cascade/provider-requests', { params }),
}
