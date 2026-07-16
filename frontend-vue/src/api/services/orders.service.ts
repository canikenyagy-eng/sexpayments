import api from '@/api/client'
import type { Order, OrderDebug, OrderStatus, Paginated, PaymentMethod } from '@/types'

export interface ReceiptItem {
  uuid: string
  created_at: string
  source: string
  filename: string
}

export const ordersService = {
  // All receipts attached to an order (trader sees only premoderation-approved).
  listReceipts: (orderUuid: string) =>
    api.get<ReceiptItem[]>(`/api/v1/orders/${orderUuid}/receipts`),

  list: (params?: {
    skip?: number
    limit?: number
    merchant_id?: number
    trader_id?: number
    status?: OrderStatus
    search?: string
    id_search?: string
    trader_login?: string
    merchant_login?: string
    payment_method?: PaymentMethod
    amount_from?: number
    amount_to?: number
  }) => api.get<Paginated<Order>>('/api/v1/orders/', { params }),

  debug: (identifier: string) =>
    api.get<OrderDebug>(`/api/v1/orders/debug/${identifier}`),

  updateAdmin: (orderId: number, data: { status?: string; amount?: number; reason?: string }) =>
    api.patch<Order>(`/api/v1/orders/${orderId}`, data),

  getMyActive: () => api.get<Order[]>('/api/v1/orders/my-active'),

  listMy: (params?: {
    skip?: number
    limit?: number
    status?: OrderStatus
    payment_method?: PaymentMethod
    id_search?: string
    amount_from?: number
    amount_to?: number
  }) => api.get<Paginated<Order>>('/api/v1/orders/my', { params }),

  listMyDisputed: (params?: { skip?: number; limit?: number }) =>
    api.get<Order[]>('/api/v1/orders/my-disputes', { params }),

  confirmSuccess: (orderId: number) =>
    api.post<Order>(`/api/v1/orders/${orderId}/success`),

  // Settle an already failed/canceled order to success (late payment arrived).
  settleFailed: (orderId: number) =>
    api.post<Order>(`/api/v1/orders/${orderId}/settle`),
}
