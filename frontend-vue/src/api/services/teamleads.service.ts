import api from '@/api/client'
import type {
  TeamleadAdminItem,
  TeamleadLink,
  TeamleadLinkCreate,
  TeamleadLinkEnriched,
  TeamleadReward,
  TeamleadStats,
  TeamleadTraderOrderFinance,
} from '@/types'

export const teamleadsService = {
  // Admin
  listAllLinks: (params?: { skip?: number; limit?: number; teamlead_id?: number }) =>
    api.get<TeamleadLink[]>('/api/v1/teamleaders/admin/links', { params }),

  listAdmin: (params?: {
    skip?: number
    limit?: number
    search?: string
    is_active?: boolean
    balance_from?: number
    balance_to?: number
  }) => api.get<TeamleadAdminItem[]>('/api/v1/teamleaders/admin/teamleads', { params }),

  createLink: (data: TeamleadLinkCreate) =>
    api.post<TeamleadLink>('/api/v1/teamleaders/admin/links', data),

  updateLink: (linkId: number, data: { fee_percent?: number; payout_fee_percent?: number; is_active?: boolean }) =>
    api.patch<TeamleadLink>(`/api/v1/teamleaders/admin/links/${linkId}`, data),

  deleteLink: (linkId: number) =>
    api.delete<void>(`/api/v1/teamleaders/admin/links/${linkId}`),

  // Teamlead self
  getMyLinks: () => api.get<TeamleadLink[]>('/api/v1/teamleaders/my-links'),

  getMyLinksEnriched: () =>
    api.get<TeamleadLinkEnriched[]>('/api/v1/teamleaders/my-links-enriched'),

  getMyStats: () => api.get<TeamleadStats>('/api/v1/teamleaders/my-stats'),

  getMyTraderOrders: (params?: { skip?: number; limit?: number }) =>
    api.get<TeamleadTraderOrderFinance[]>('/api/v1/teamleaders/my-trader-orders', { params }),

  getMyRewards: () => api.get<TeamleadReward[]>('/api/v1/teamleaders/my-rewards'),
}
