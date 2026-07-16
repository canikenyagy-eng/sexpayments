import api from '@/api/client'
import type { RateConfig, RateConfigCreate, RateConfigUpdate } from '@/types'

export const ratesService = {
  list: () => api.get<RateConfig[]>('/api/v1/rates/'),

  getById: (id: number) => api.get<RateConfig>(`/api/v1/rates/${id}`),

  create: (data: RateConfigCreate) => api.post<RateConfig>('/api/v1/rates/', data),

  update: (id: number, data: RateConfigUpdate) =>
    api.patch<RateConfig>(`/api/v1/rates/${id}`, data),

  remove: (id: number) => api.delete(`/api/v1/rates/${id}`),

  sync: (id: number) => api.post<RateConfig>(`/api/v1/rates/${id}/sync`),
}
