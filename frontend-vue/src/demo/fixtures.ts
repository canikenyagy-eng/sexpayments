import { demoUser } from './config'

const now = Date.now()

export const demoUsers = [
  demoUser,
  {
    id: 2,
    username: 'мерчант_альфа',
    role: 'merchant',
    totp_enabled: true,
    is_blocked: false,
    use_shared_balance: false,
    timezone: 'Europe/Moscow',
    created_at: '2026-07-01T09:20:00Z',
    balance_usdt: 84200,
  },
  {
    id: 3,
    username: 'трейдер_север',
    role: 'trader',
    totp_enabled: true,
    is_blocked: false,
    use_shared_balance: false,
    timezone: 'Europe/Moscow',
    created_at: '2026-07-02T12:10:00Z',
    balance_usdt: 53800,
  },
]

export const demoMerchants = [
  {
    id: 101,
    user_id: 2,
    name: 'Закрытая медиа-группа',
    status: 'enabled',
    is_active: true,
    currency: 'USDT',
    api_key_masked: 'sp_live_••••••••9D2F',
  },
  {
    id: 102,
    user_id: 2,
    name: 'Кабинет авторов',
    status: 'test',
    is_active: true,
    currency: 'USDT',
    api_key_masked: 'sp_live_••••••••41AE',
  },
]

export const demoStats = {
  turnover_usdt: 8420000,
  profit_usdt: 312400,
  requests_rub: 128940,
  requests_rub_24h: 6240,
  payout_pct: 71,
  payin_requests_total: 78320,
  payin_requests_24h: 3120,
  orders_total: 28460,
  orders_success: 26730,
  orders_active: 148,
  conversion_pct: 93.9,
  merchants_online_24h: 18,
  traders_online_24h: 42,
  pending_withdrawals: 7,
  active_disputes: 11,
}

export const demoTimeseries = {
  granularity: 'day',
  points: Array.from({ length: 14 }, (_, index) => {
    const day = 13 - index
    const ts = Math.floor((now - day * 24 * 60 * 60 * 1000) / 1000)
    const turnover = 420000 + index * 28500 + (index % 3) * 36000
    return {
      ts,
      turnover_usdt: turnover,
      profit_usdt: Math.round(turnover * 0.038),
      orders: 940 + index * 42,
      provider_turnover_usdt: Math.round(turnover * 0.34),
      provider_profit_usdt: Math.round(turnover * 0.012),
      provider_orders: 220 + index * 11,
    }
  }),
}

export const demoOrders = [
  {
    id: 7301,
    uuid: 'sp-demo-7301',
    external_id: 'checkout-7301',
    merchant_id: 101,
    trader_id: 3,
    amount: 42900,
    currency: 'RUB',
    status: 'success',
    payment_method: 'card',
    created_at: new Date(now - 18 * 60 * 1000).toISOString(),
  },
  {
    id: 7300,
    uuid: 'sp-demo-7300',
    external_id: 'checkout-7300',
    merchant_id: 102,
    trader_id: 3,
    amount: 18500,
    currency: 'RUB',
    status: 'pending',
    payment_method: 'sbp',
    created_at: new Date(now - 42 * 60 * 1000).toISOString(),
  },
  {
    id: 7299,
    uuid: 'sp-demo-7299',
    external_id: 'checkout-7299',
    merchant_id: 101,
    trader_id: 3,
    amount: 9600,
    currency: 'RUB',
    status: 'receipt_uploaded',
    payment_method: 'card',
    created_at: new Date(now - 86 * 60 * 1000).toISOString(),
  },
  {
    id: 7298,
    uuid: 'sp-demo-7298',
    external_id: 'checkout-7298',
    merchant_id: 101,
    trader_id: 3,
    amount: 122000,
    currency: 'RUB',
    status: 'disputed',
    payment_method: 'sbp',
    created_at: new Date(now - 142 * 60 * 1000).toISOString(),
  },
]

export const demoWithdrawals = [
  {
    id: 501,
    user_id: 2,
    user_role: 'merchant',
    amount: 18400,
    currency: 'USDT',
    destination_address: 'TQd9...B8x2',
    status: 'pending',
    created_at: new Date(now - 35 * 60 * 1000).toISOString(),
  },
  {
    id: 500,
    user_id: 3,
    user_role: 'trader',
    amount: 7600,
    currency: 'USDT',
    destination_address: 'TXs4...91Kp',
    status: 'pending',
    created_at: new Date(now - 2 * 60 * 60 * 1000).toISOString(),
  },
]

export const demoBalances = [
  { id: 1, user_id: 1, type: 'work', currency: 'USDT', amount: 125000 },
  { id: 2, user_id: 1, type: 'escrow', currency: 'USDT', amount: 38000 },
]

export const demoActiveStats = {
  orders_active: 148,
  active_disputes: 11,
  pending_withdrawals: 7,
  requisites_traffic_active: 64,
}

export const demoPrimeTime = {
  active: true,
  points: 1.5,
  ends_at: new Date(now + 42 * 60 * 1000).toISOString(),
}
