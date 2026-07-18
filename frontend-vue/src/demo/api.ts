import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { demoTokens, demoUser, findDemoAccountByToken, isDemoMode } from './config'
import {
  demoActiveStats,
  demoBalances,
  demoMerchantBalances,
  demoMerchantOrders,
  demoMerchantProfile,
  demoMerchantStats,
  demoMerchants,
  demoOrders,
  demoPrimeTime,
  demoStats,
  demoTeamleadBalances,
  demoTeamleadLinks,
  demoTeamleadRewards,
  demoTeamleadStats,
  demoTimeseries,
  demoTraderAchievements,
  demoTraderActiveOrders,
  demoTraderBalances,
  demoTraderProfile,
  demoUsers,
  demoWithdrawals,
} from './fixtures'

function response<T>(
  config: InternalAxiosRequestConfig,
  data: T,
  status = 200,
): AxiosResponse<T> {
  return {
    data,
    status,
    statusText: status >= 400 ? 'Demo Error' : 'OK',
    headers: {},
    config,
  }
}

function pathFromConfig(config: InternalAxiosRequestConfig): string {
  const rawUrl = config.url || '/'
  return new URL(rawUrl, 'https://sexpayments.demo').pathname
}

function tokenFromConfig(config: InternalAxiosRequestConfig): string | null {
  const headers = config.headers as Record<string, string | undefined> | undefined
  const authorization = headers?.Authorization || headers?.authorization
  return authorization?.replace(/^Bearer\s+/i, '') || null
}

function currentUser(config: InternalAxiosRequestConfig) {
  return findDemoAccountByToken(tokenFromConfig(config))?.user ?? demoUser
}

function balancesForRole(role: string) {
  if (role === 'merchant') return demoMerchantBalances
  if (role === 'trader') return demoTraderBalances
  if (role === 'teamlead') return demoTeamleadBalances
  return demoBalances
}

export const demoAdapter: AxiosAdapter = async (config) => {
  const path = pathFromConfig(config)
  const method = (config.method || 'get').toLowerCase()
  const user = currentUser(config)

  if (path === '/api/v1/auth/refresh') {
    return response(config, {
      access_token: demoTokens.access,
      refresh_token: demoTokens.refresh,
      token_type: 'bearer',
      user: demoUser,
    })
  }

  if (path === '/api/v1/auth/logout') {
    return response(config, { ok: true })
  }

  if (path === '/api/v1/users/me') return response(config, user)
  if (path === '/api/v1/users/') return response(config, demoUsers)
  if (path === '/api/v1/merchants/') return response(config, demoMerchants)
  if (path === '/api/v1/merchants/me/profile') return response(config, demoMerchantProfile)
  if (path === '/api/v1/merchants/me/stats') return response(config, demoMerchantStats)
  if (path === '/api/v1/merchants/me/merchants') return response(config, demoMerchants)
  if (path === '/api/v1/merchants/me/orders') {
    return response(config, { items: demoMerchantOrders, total: demoMerchantOrders.length })
  }
  if (path === '/api/v1/merchants/me/withdrawals') {
    return response(config, demoWithdrawals.filter(item => item.user_role === 'merchant'))
  }
  if (path === '/api/v1/traders/me') return response(config, demoTraderProfile)
  if (path === '/api/v1/traders/me/achievements') return response(config, demoTraderAchievements)
  if (path === '/api/v1/orders/my-active') return response(config, demoTraderActiveOrders)
  if (path === '/api/v1/teamleaders/my-stats') return response(config, demoTeamleadStats)
  if (path === '/api/v1/teamleaders/my-links-enriched') return response(config, demoTeamleadLinks)
  if (path === '/api/v1/teamleaders/my-links') return response(config, demoTeamleadLinks)
  if (path === '/api/v1/teamleaders/my-rewards') return response(config, demoTeamleadRewards)
  if (path === '/api/v1/teamleaders/my-trader-orders') return response(config, [])
  if (path === '/api/v1/stats/admin') return response(config, demoStats)
  if (path === '/api/v1/stats/admin/timeseries') return response(config, demoTimeseries)
  if (path === '/api/v1/stats/me/active') return response(config, demoActiveStats)
  if (path === '/api/v1/orders/') {
    return response(config, { items: demoOrders, total: demoOrders.length })
  }
  if (path === '/api/v1/finances/withdrawals') return response(config, demoWithdrawals)
  if (path === '/api/v1/finances/my-balances') return response(config, balancesForRole(user.role))
  if (path === '/api/v1/platform-settings/primetime') return response(config, demoPrimeTime)

  if (method === 'patch' && path === '/api/v1/traders/me/payin') {
    return response(config, { ...demoTraderProfile, is_payin_active: true })
  }

  if (method === 'patch' && path === '/api/v1/traders/me/payout') {
    return response(config, { ...demoTraderProfile, is_payout_active: true })
  }

  if (method === 'get') {
    if (path.includes('/orders') || path.includes('/payouts')) {
      return response(config, { items: [], total: 0 })
    }
    if (
      path.includes('/withdrawals') ||
      path.includes('/users') ||
      path.includes('/merchants') ||
      path.includes('/traders') ||
      path.includes('/disputes') ||
      path.includes('/requisites') ||
      path.includes('/callbacks') ||
      path.includes('/logs') ||
      path.includes('/providers') ||
      path.includes('/groups')
    ) {
      return response(config, [])
    }
    return response(config, {})
  }

  return response(config, {
    ok: true,
    demo: true,
    message: 'Демо-режим: действие показано без изменения данных.',
  })
}

export function demoApiConfig() {
  return isDemoMode ? { adapter: demoAdapter } : {}
}
