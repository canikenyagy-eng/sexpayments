import api from '@/api/client'
import type {
  ModerationDecision,
  ReceiptModerationItem,
  ReceiptModerationListParams,
  ReceiptModerationListResponse,
} from '@/types'

/**
 * Admin-side calls for /api/v1/receipt-moderations.
 *
 * Browse the history + per-order details, and apply a decision (Accept /
 * Request-PDF / Request-Video) on a pending check — the web equivalent of the
 * support-bot inline buttons. The decision runs through the same shared backend
 * coordinator the bot uses, so web and Telegram never drift.
 */
export const receiptModerationsService = {
  list: (params: ReceiptModerationListParams = {}) =>
    api.get<ReceiptModerationListResponse>('/api/v1/receipt-moderations', { params }),

  listForOrder: (orderId: number) =>
    api.get<ReceiptModerationItem[]>(`/api/v1/receipt-moderations/order/${orderId}`),

  applyDecision: (orderId: number, decision: ModerationDecision) =>
    api.post<ReceiptModerationItem>(
      `/api/v1/receipt-moderations/${orderId}/decide`,
      { decision },
    ),
}
