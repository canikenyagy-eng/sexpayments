import api from '@/api/client'
import type { Trader, TraderAchievements, TraderUpdateAdmin, TraderGroup } from '@/types'

export const tradersService = {
  list: (params?: { skip?: number; limit?: number; search?: string }) =>
    api.get<Trader[]>('/api/v1/traders/', { params }),

  getById: (id: number) => api.get<Trader>(`/api/v1/traders/${id}`),

  update: (id: number, data: TraderUpdateAdmin) =>
    api.patch<Trader>(`/api/v1/traders/${id}`, data),

  getMe: () => api.get<Trader>('/api/v1/traders/me'),

  getMyAchievements: () => api.get<TraderAchievements>('/api/v1/traders/me/achievements'),

  recomputeAchievements: () =>
    api.post<{ changed: number }>('/api/v1/traders/achievements/recompute'),

  togglePayin: (is_active: boolean) =>
    api.patch<Trader>('/api/v1/traders/me/payin', { is_active }),

  togglePayout: (is_active: boolean) =>
    api.patch<Trader>('/api/v1/traders/me/payout', { is_active }),

  toggleReceiptAutoCheck: (enabled: boolean) =>
    api.patch<Trader>('/api/v1/traders/me/receipt-auto-check', { enabled }),

  setDefaultReceiptProvider: (providerId: number | null) =>
    api.patch<Trader>('/api/v1/traders/me/receipt-check-provider', {
      provider_id: providerId,
    }),

  listGroups: () => api.get<TraderGroup[]>('/api/v1/traders/groups'),

  createGroup: (name: string, description?: string) =>
    api.post<TraderGroup>('/api/v1/traders/groups', { name, description }),

  updateGroup: (
    id: number,
    data: { name?: string; description?: string; trader_ids?: number[]; merchant_ids?: number[] },
  ) => api.patch<TraderGroup>(`/api/v1/traders/groups/${id}`, data),

  deleteGroup: (id: number) =>
    api.delete<void>(`/api/v1/traders/groups/${id}`),

  addTraderToGroup: (groupId: number, traderId: number) =>
    api.post<TraderGroup>(`/api/v1/traders/groups/${groupId}/traders/${traderId}`),

  addMerchantToGroup: (groupId: number, merchantId: number) =>
    api.post<TraderGroup>(`/api/v1/traders/groups/${groupId}/merchants/${merchantId}`),

  removeTraderFromGroup: (groupId: number, traderId: number) =>
    api.delete<TraderGroup>(`/api/v1/traders/groups/${groupId}/traders/${traderId}`),

  removeMerchantFromGroup: (groupId: number, merchantId: number) =>
    api.delete<TraderGroup>(`/api/v1/traders/groups/${groupId}/merchants/${merchantId}`),
}
