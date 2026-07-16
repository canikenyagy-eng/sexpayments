import api from '@/api/client'
import type {
  AdminPayout,
  Paginated,
  PayoutReceiptItem,
  PayoutTerminal,
  PayoutTerminalCreate,
  TraderPayout,
  TraderPayoutPoolItem,
} from '@/types'

export const payoutsService = {
  // ── admin: terminals ──
  listTerminals: (params?: { owner_id?: number; skip?: number; limit?: number }) =>
    api.get<PayoutTerminal[]>('/api/v1/payouts/terminals', { params }),

  getTerminal: (id: number) =>
    api.get<PayoutTerminal>(`/api/v1/payouts/terminals/${id}`),

  createTerminal: (data: PayoutTerminalCreate) =>
    api.post<PayoutTerminal>('/api/v1/payouts/terminals', data),

  updateTerminal: (id: number, data: Partial<PayoutTerminalCreate> & { status?: string }) =>
    api.patch<PayoutTerminal>(`/api/v1/payouts/terminals/${id}`, data),

  topupTerminal: (id: number, amount: number) =>
    api.post<PayoutTerminal>(`/api/v1/payouts/terminals/${id}/topup`, { amount }),

  // ── admin: payouts ──
  listPayouts: (params?: { status?: string; terminal_id?: number; trader_id?: number; skip?: number; limit?: number }) =>
    api.get<Paginated<AdminPayout>>('/api/v1/payouts', { params }),

  listReceipts: (uuid: string) =>
    api.get<PayoutReceiptItem[]>(`/api/v1/payouts/${uuid}/receipts`),

  approveReceipt: (receiptId: number) =>
    api.post<AdminPayout>(`/api/v1/payouts/receipts/${receiptId}/approve`),

  rejectReceipt: (receiptId: number, reason: string) =>
    api.post<AdminPayout>(`/api/v1/payouts/receipts/${receiptId}/reject`, { reason }),

  adminCancel: (uuid: string, reason?: string) =>
    api.post<AdminPayout>(`/api/v1/payouts/${uuid}/cancel`, reason ? { reason } : {}),

  adminComplete: (uuid: string) =>
    api.post<AdminPayout>(`/api/v1/payouts/${uuid}/complete`),

  // ── trader ──
  pool: (params?: { skip?: number; limit?: number }) =>
    api.get<TraderPayoutPoolItem[]>('/api/v1/payouts/pool', { params }),

  mine: (params?: { skip?: number; limit?: number }) =>
    api.get<TraderPayout[]>('/api/v1/payouts/mine', { params }),

  claim: (uuid: string) => api.post<TraderPayout>(`/api/v1/payouts/${uuid}/claim`),

  uploadReceipt: (uuid: string, amount: number, file: File) => {
    const form = new FormData()
    form.append('amount', String(amount))
    form.append('attachment', file)
    return api.post<TraderPayout>(`/api/v1/payouts/${uuid}/receipt`, form, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
}
