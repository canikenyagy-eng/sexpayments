import type { AxiosAdapter, AxiosResponse, InternalAxiosRequestConfig } from 'axios'
import { demoTokens, demoUser, isDemoMode } from './config'
import {
  demoActiveStats,
  demoBalances,
  demoMerchants,
  demoOrders,
  demoPrimeTime,
  demoStats,
  demoTimeseries,
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

export const demoAdapter: AxiosAdapter = async (config) => {
  const path = pathFromConfig(config)
  const method = (config.method || 'get').toLowerCase()

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

  if (path === '/api/v1/users/me') return response(config, demoUser)
  if (path === '/api/v1/users/') return response(config, demoUsers)
  if (path === '/api/v1/merchants/') return response(config, demoMerchants)
  if (path === '/api/v1/stats/admin') return response(config, demoStats)
  if (path === '/api/v1/stats/admin/timeseries') return response(config, demoTimeseries)
  if (path === '/api/v1/stats/me/active') return response(config, demoActiveStats)
  if (path === '/api/v1/orders/') {
    return response(config, { items: demoOrders, total: demoOrders.length })
  }
  if (path === '/api/v1/finances/withdrawals') return response(config, demoWithdrawals)
  if (path === '/api/v1/finances/my-balances') return response(config, demoBalances)
  if (path === '/api/v1/platform-settings/primetime') return response(config, demoPrimeTime)

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
