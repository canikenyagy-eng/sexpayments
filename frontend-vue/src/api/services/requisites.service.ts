import api from '@/api/client'
import type { PaymentMethod, Requisite, RequisiteCreate, RequisiteStatus, RequisiteUpdate } from '@/types'

export const requisitesService = {
  // Trader own requisites
  listMy: (params?: {
    skip?: number
    limit?: number
    nickname?: string
    bank?: string
    payment_method?: PaymentMethod
    status?: RequisiteStatus
    is_active?: boolean
    state?: 'active' | 'disabled' | 'blocked' | 'archived'
    ready_only?: boolean
  }) => api.get<Requisite[]>('/api/v1/requisites/me', { params }),

  getMy: (id: number) => api.get<Requisite>(`/api/v1/requisites/me/${id}`),

  create: (data: RequisiteCreate) =>
    api.post<Requisite>('/api/v1/requisites/me', data),

  updateMy: (id: number, data: RequisiteUpdate) =>
    api.patch<Requisite>(`/api/v1/requisites/me/${id}`, data),

  deleteMy: (id: number) => api.delete(`/api/v1/requisites/me/${id}`),

  enableMy: (id: number) =>
    api.post<Requisite>(`/api/v1/requisites/me/${id}/enable`),

  disableMy: (id: number) =>
    api.post<Requisite>(`/api/v1/requisites/me/${id}/disable`),

  // Admin requisites
  listAll: (params?: {
    skip?: number
    limit?: number
    trader_login?: string
    payment_method?: PaymentMethod
    is_active?: boolean
    is_enabled?: boolean
    bank?: string
  }) => api.get<Requisite[]>('/api/v1/requisites/', { params }),

  getById: (id: number) => api.get<Requisite>(`/api/v1/requisites/${id}`),

  update: (id: number, data: RequisiteUpdate) =>
    api.patch<Requisite>(`/api/v1/requisites/${id}`, data),

  remove: (id: number) => api.delete(`/api/v1/requisites/${id}`),
}
