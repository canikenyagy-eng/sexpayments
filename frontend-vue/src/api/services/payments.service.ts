import api from '@/api/client'
import type { PaymentOption } from '@/types'

export const paymentsService = {
  getOptions: () => api.get<PaymentOption[]>('/api/v1/payments/options'),
}
