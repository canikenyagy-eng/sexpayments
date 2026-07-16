import api from '@/api/client'
import type { ReceiptCheck, TraderReceiptProvider } from '@/types'

/**
 * Trader-facing endpoints around a single order's receipt verification.
 *
 * `listProviders` returns the ACTIVE providers (name + price) the trader may
 * choose from — an empty list means the service is unavailable.
 */
export const receiptChecksService = {
  listProviders: () =>
    api.get<TraderReceiptProvider[]>('/api/v1/orders/receipt-check/providers'),

  getLatest: (orderId: string) =>
    api.get<ReceiptCheck | null>(`/api/v1/orders/${orderId}/receipt-check`),

  getLatestBulk: (orderUuids: string[]) =>
    api.post<Record<string, ReceiptCheck | null>>(
      '/api/v1/orders/receipt-check/bulk-latest',
      { order_uuids: orderUuids },
    ),

  runManual: (orderId: string, providerId: number | null) =>
    api.post<ReceiptCheck>(`/api/v1/orders/${orderId}/receipt-check`, {
      confirm: true,
      provider_id: providerId,
    }),
}
