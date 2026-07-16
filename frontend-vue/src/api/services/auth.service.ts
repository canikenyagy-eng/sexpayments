import api from '@/api/client'
import type { TokenResponse, User } from '@/types'

export const authService = {
  login: (username: string, password: string, totp_code?: string) =>
    api.post<TokenResponse>('/api/v1/auth/login', { username, password, totp_code }),

  register: (username: string, password: string, role: string) =>
    api.post<User>('/api/v1/auth/register', { username, password, role }),

  refresh: (refresh_token: string) =>
    api.post<TokenResponse>('/api/v1/auth/refresh', { refresh_token }),

  logout: (refresh_token: string) =>
    api.post('/api/v1/auth/logout', { refresh_token }),

  getMe: () => api.get<User>('/api/v1/users/me'),

  impersonate: (target_user_id: number) =>
    api.post<TokenResponse>('/api/v1/auth/impersonate', { target_user_id }),
}
