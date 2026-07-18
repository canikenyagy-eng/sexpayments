import type { User } from '@/types'

export const isDemoMode = import.meta.env.VITE_DEMO_MODE === 'true'

export interface DemoAccount {
  label: string
  username: string
  password: string
  user: User
  tokens: {
    access: string
    refresh: string
  }
}

export const demoAccounts: DemoAccount[] = [
  {
    label: 'Администратор',
    username: 'admin',
    password: 'DemoAdmin2026!',
    user: {
      id: 1,
      username: 'admin',
      role: 'admin',
      totp_enabled: false,
      is_blocked: false,
      use_shared_balance: false,
      timezone: 'Europe/Moscow',
      created_at: '2026-07-17T00:00:00Z',
      balance_usdt: 125000,
    },
    tokens: {
      access: 'demo-access-token',
      refresh: 'demo-refresh-token',
    },
  },
  {
    label: 'Мерчант',
    username: 'merchant',
    password: 'DemoMerchant2026!',
    user: {
      id: 2,
      username: 'мерчант_альфа',
      role: 'merchant',
      totp_enabled: false,
      is_blocked: false,
      use_shared_balance: false,
      timezone: 'Europe/Moscow',
      created_at: '2026-07-01T09:20:00Z',
      balance_usdt: 84200,
    },
    tokens: {
      access: 'demo-merchant-access-token',
      refresh: 'demo-merchant-refresh-token',
    },
  },
  {
    label: 'Трейдер',
    username: 'trader',
    password: 'DemoTrader2026!',
    user: {
      id: 3,
      username: 'трейдер_север',
      role: 'trader',
      totp_enabled: false,
      is_blocked: false,
      use_shared_balance: false,
      timezone: 'Europe/Moscow',
      created_at: '2026-07-02T12:10:00Z',
      balance_usdt: 53800,
    },
    tokens: {
      access: 'demo-trader-access-token',
      refresh: 'demo-trader-refresh-token',
    },
  },
  {
    label: 'Тимлид',
    username: 'teamlead',
    password: 'DemoTeamlead2026!',
    user: {
      id: 4,
      username: 'тимлид_операций',
      role: 'teamlead',
      totp_enabled: false,
      is_blocked: false,
      use_shared_balance: false,
      timezone: 'Europe/Moscow',
      created_at: '2026-07-03T08:30:00Z',
      balance_usdt: 21800,
    },
    tokens: {
      access: 'demo-teamlead-access-token',
      refresh: 'demo-teamlead-refresh-token',
    },
  },
]

export const demoCredentials = {
  username: demoAccounts[0].username,
  password: demoAccounts[0].password,
}

export const demoUser = demoAccounts[0].user
export const demoTokens = demoAccounts[0].tokens

export function findDemoAccountByCredentials(username: string, password: string) {
  return demoAccounts.find(account => account.username === username && account.password === password)
}

export function findDemoAccountByToken(token: string | null | undefined) {
  return demoAccounts.find(account => account.tokens.access === token || account.tokens.refresh === token)
}

export function isDemoToken(token: string | null | undefined): boolean {
  return !!findDemoAccountByToken(token)
}
