import api from '@/api/client'
import type { CallbackAttempt } from '@/types'

export const callbacksService = {
  listAttempts: (params?: {
    skip?: number
    limit?: number
    order_id?: number
    is_successful?: boolean
    sort_order?: 'asc' | 'desc'
  }) =>
    api.get<CallbackAttempt[]>('/api/v1/callbacks/attempts', { params }),

  resendForOrder: (orderId: number) =>
    api.post<{ message: string }>(`/api/v1/callbacks/resend/order/${orderId}`),

  resendForMerchant: (merchantId: number, start_time: string, end_time?: string) =>
    api.post<{ message: string }>(`/api/v1/callbacks/resend/merchant/${merchantId}`, null, {
      params: { start_time, end_time },
    }),
}
