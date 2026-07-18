import type { User } from '@/types'

export const isDemoMode = import.meta.env.VITE_DEMO_MODE === 'true'

export const demoCredentials = {
  username: 'admin',
  password: 'DemoAdmin2026!',
}

export const demoUser: User = {
  id: 1,
  username: demoCredentials.username,
  role: 'admin',
  totp_enabled: false,
  is_blocked: false,
  use_shared_balance: false,
  timezone: 'Europe/Moscow',
  created_at: '2026-07-17T00:00:00Z',
  balance_usdt: 125000,
}

export const demoTokens = {
  access: 'demo-access-token',
  refresh: 'demo-refresh-token',
}

export function isDemoToken(token: string | null | undefined): boolean {
  return token === demoTokens.access || token === demoTokens.refresh
}
