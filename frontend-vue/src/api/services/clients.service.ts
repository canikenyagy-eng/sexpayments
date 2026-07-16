import api from '@/api/client'
import type { Client, OrderClientInfo, TraderOrderClientInfo } from '@/types'

export const clientsService = {
  // ── Admin ──
  listAll: (params?: {
    skip?: number
    limit?: number
    merchant_id?: number
    search?: string
    is_blocked?: boolean
    sort_by?: string
    sort_order?: 'asc' | 'desc'
  }) => api.get<Client[]>('/api/v1/clients', { params }),

  // Client block for the admin order modal. Server-gated by the merchant's
  // «Уникальные клиенты» toggle — returns null when off / no client / not yet
  // materialised, so the modal simply hides the «Клиент» section.
  getForOrder: (orderId: number) =>
    api.get<OrderClientInfo | null>(`/api/v1/clients/for-order/${orderId}`),

  // Trader variant — the trader's OWN order only; restricted payload.
  getForOrderMine: (orderId: number) =>
    api.get<TraderOrderClientInfo | null>(`/api/v1/clients/for-order/${orderId}/me`),

  // Block / unblock by the natural key (merchant_id, client_user_id) — works
  // from the Clients page and the order modal alike.
  block: (data: { merchant_id: number; client_user_id: string; reason?: string }) =>
    api.post<Client>('/api/v1/clients/block', data),

  unblock: (data: { merchant_id: number; client_user_id: string }) =>
    api.post<Client>('/api/v1/clients/unblock', data),
}
