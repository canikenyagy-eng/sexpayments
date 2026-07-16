import api from '@/api/client'
import type { User, UserUpdate, UserMeUpdate, UserRole, Setup2FAResponse } from '@/types'

export const usersService = {
  create: (data: { username: string; password: string; role: UserRole; fee_percent?: number; insurance_deposit?: number; payment_method?: string; teamlead_id?: number; teamlead_fee_percent?: number }) =>
    api.post<User>('/api/v1/auth/register', data),

  list: (params?: {
    skip?: number
    limit?: number
    role?: UserRole
    is_blocked?: boolean
    is_active?: boolean
    balance_from?: number
    balance_to?: number
    search?: string
  }) => api.get<User[]>('/api/v1/users/', { params }),

  getById: (id: number) => api.get<User>(`/api/v1/users/${id}`),

  update: (id: number, data: UserUpdate) => api.patch<User>(`/api/v1/users/${id}`, data),

  updateMe: (data: UserMeUpdate) => api.patch<User>('/api/v1/users/me', data),

  changePassword: (new_password: string, google_code: string) =>
    api.post<User>('/api/v1/users/me/password', { new_password, google_code }),

  setup2FA: () => api.get<Setup2FAResponse>('/api/v1/users/me/2fa/setup'),

  enable2FA: (secret: string, google_code: string) =>
    api.post<User>('/api/v1/users/me/2fa/enable', { secret, google_code }),

  reset2FA: (userId: number) => api.post<User>(`/api/v1/users/${userId}/2fa/reset`),
}
