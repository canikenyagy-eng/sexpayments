import api from '@/api/client'
import type {
  AdminDoliv,
  Doliv,
  DolivAccess,
  DolivCreateRequest,
  DolivLimits,
  Paginated,
  PayoutStatus,
} from '@/types'

export interface AdminDolivParams {
  status?: PayoutStatus
  requester_trader_id?: number
  executor_trader_id?: number
  created_from?: string
  created_to?: string
  search?: string
  skip?: number
  limit?: number
}

/**
 * Долив (requisite refill) — /api/v1/doliv.
 *
 * Requester (any trader): create a долив against their own requisite.
 * Доливщик (trader in the platform-settings executor list): see the «Долив» tab
 * (open pool + their taken доливы with result) and claim / execute. The tab is
 * gated by `access().is_executor`; the executor list lives in admin settings.
 */
export const dolivService = {
  // ── requester ──
  create: (data: DolivCreateRequest) => api.post<Doliv>('/api/v1/doliv', data),

  // Долив amount bounds (min/max) — used by the «Максимум» quick-fill.
  config: () => api.get<DolivLimits>('/api/v1/doliv/config'),

  // The trader's own доливы (as requester) — the «Мои доливы» tab.
  mine: (params?: { skip?: number; limit?: number }) =>
    api.get<Doliv[]>('/api/v1/doliv/mine', { params }),

  // Cancel an unclaimed (CREATED) долив → refund. Once it's «В работе» the
  // backend rejects it (only an admin can then close it via the state machine).
  cancel: (uuid: string) => api.post<Doliv>(`/api/v1/doliv/${uuid}/cancel`),

  // ── доливщик ──
  access: () => api.get<DolivAccess>('/api/v1/doliv/access'),

  executor: (params?: { skip?: number; limit?: number }) =>
    api.get<Doliv[]>('/api/v1/doliv/executor', { params }),

  claim: (uuid: string) => api.post<Doliv>(`/api/v1/doliv/${uuid}/claim`),

  // Execute = confirm the transfer by attaching the receipt (mandatory). The
  // receipt is shown to the requester + pushed to their telegram bot.
  execute: (uuid: string, file: File) => {
    const form = new FormData()
    form.append('attachment', file)
    return api.post<Doliv>(`/api/v1/doliv/${uuid}/execute`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },

  // Download the долив receipt (requester or доливщик).
  receiptUrl: (uuid: string) => `/api/v1/doliv/${uuid}/receipt`,

  // ── admin ──
  // Filtered, paginated доливы list with resolved trader usernames.
  all: (params?: AdminDolivParams) =>
    api.get<Paginated<AdminDoliv>>('/api/v1/doliv/all', { params }),

  // Admin free status machine: move a долив to ANY status; the backend applies
  // the money (settle / refund / reverse) for the transition.
  adminChangeStatus: (uuid: string, status: PayoutStatus) =>
    api.post<AdminDoliv>(`/api/v1/doliv/${uuid}/admin/status`, { status }),
}
