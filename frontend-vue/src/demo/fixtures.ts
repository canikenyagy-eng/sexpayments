import { demoAccounts } from './config'

const now = Date.now()

export const demoUsers = demoAccounts.map(account => account.user)

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

export const demoMerchantProfile = {
  id: 101,
  name: 'Закрытая медиа-группа',
  status: 'enabled',
  currency: 'USDT',
  webhook_url: 'https://merchant.example/callback',
  api_key_masked: 'sp_live_••••••••9D2F',
  balance_work: 84200,
  balance_escrow: 18400,
  order_ttl_seconds: 900,
  requisite_search_timeout_ms: 12000,
  fees: {
    card: 4.2,
    sbp: 3.8,
    sim: 5.1,
  },
  withdrawal_fee_fixed: 7,
  payment_methods: [
    { method: 'card', fee_percentage: 4.2 },
    { method: 'sbp', fee_percentage: 3.8 },
    { method: 'sim', fee_percentage: 5.1 },
  ],
  telegram_user_ids: [],
}

export const demoMerchantStats = {
  turnover_usdt: 1248000,
  fee_usdt: 48720,
  orders_total: 6420,
  orders_success: 6048,
  orders_active: 38,
  orders_failed: 212,
  conversion_pct: 94.2,
  pending_withdrawals: 3,
  active_disputes: 4,
  requests_rub: 39400,
  payin_requests_total: 6420,
  payout_pct: 74,
}

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

export const demoTraderActiveOrders = demoOrders
  .filter(order => order.status !== 'success')
  .map(order => ({
    ...order,
    direction: 'payin',
    merchant_name: 'Закрытая медиа-группа',
    amount_usdt: Math.round(order.amount / 92),
    updated_at: order.created_at,
    date_end: new Date(now + 12 * 60 * 1000).toISOString(),
  }))

export const demoMerchantOrders = demoOrders.map(order => ({
  ...order,
  direction: 'payin',
  merchant_name: order.merchant_id === 101 ? 'Закрытая медиа-группа' : 'Кабинет авторов',
  amount_usdt: Math.round(order.amount / 92),
  updated_at: order.created_at,
}))

export const demoReceiptModerations = [
  {
    id: 9001,
    order_id: 7299,
    order_uuid: 'sp-demo-7299',
    order_external_id: 'checkout-7299',
    merchant_id: 101,
    merchant_name: 'Закрытая медиа-группа',
    trader_id: 3,
    trader_username: 'трейдер_север',
    moderation_status: 'pending',
    has_receipt: true,
    chat_id: null,
    message_id: null,
    decision: null,
    moderator_tg_id: null,
    moderator_username: null,
    created_at: new Date(now - 11 * 60 * 1000).toISOString(),
    decided_at: null,
  },
  {
    id: 9000,
    order_id: 7301,
    order_uuid: 'sp-demo-7301',
    order_external_id: 'checkout-7301',
    merchant_id: 101,
    merchant_name: 'Закрытая медиа-группа',
    trader_id: 3,
    trader_username: 'трейдер_север',
    moderation_status: 'approved',
    has_receipt: true,
    chat_id: null,
    message_id: null,
    decision: 'accept',
    moderator_tg_id: null,
    moderator_username: 'саппорт_оператор',
    created_at: new Date(now - 45 * 60 * 1000).toISOString(),
    decided_at: new Date(now - 43 * 60 * 1000).toISOString(),
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

export const demoMerchantBalances = [
  { id: 11, user_id: 2, type: 'work', currency: 'USDT', amount: 84200 },
  { id: 12, user_id: 2, type: 'escrow', currency: 'USDT', amount: 18400 },
]

export const demoTraderBalances = [
  { id: 21, user_id: 3, type: 'work', currency: 'USDT', amount: 53800 },
  { id: 22, user_id: 3, type: 'escrow', currency: 'USDT', amount: 9600 },
]

export const demoTeamleadBalances = [
  { id: 31, user_id: 4, type: 'work', currency: 'USDT', amount: 21800 },
]

export const demoTraderProfile = {
  id: 301,
  user_id: 3,
  status: 'enabled',
  is_payin_active: true,
  is_payout_active: true,
  telegram_group_id: null,
  accept_all_merchants: false,
  receipt_auto_check: true,
  default_receipt_check_provider_id: null,
  achievement_bonus_percent: 1.5,
  priority_bonus_percent: 0.5,
  groups: [
    { id: 1, name: 'Высокое одобрение', description: 'Основной рабочий пул' },
  ],
  merchants: [
    { id: 101, name: 'Закрытая медиа-группа' },
    { id: 102, name: 'Кабинет авторов' },
  ],
  methods_config: {
    card: { fee: 2.8, min_amount: 3000, max_amount: 180000, is_active: true },
    sbp: { fee: 2.4, min_amount: 1500, max_amount: 150000, is_active: true },
    sim: { fee: 3.1, min_amount: 5000, max_amount: 220000, is_active: false },
  },
}

export const demoTraderAchievements = {
  enabled: true,
  total_bonus_percent: 1.5,
  today_volume_usdt: 18600,
  items: [{ rule_type: 'streak_volume_tier', level_key: 'gold', bonus_percent: 1.5 }],
  tiers: [
    {
      window_days: 7,
      avg_volume_usdt: 17200,
      current_index: 1,
      current_percent: 1.5,
      locked: false,
      levels: [
        { threshold_usdt: 10000, percent: 1 },
        { threshold_usdt: 15000, percent: 1.5 },
        { threshold_usdt: 25000, percent: 2 },
      ],
    },
  ],
  streaks: [
    {
      current_days: 6,
      target_days: 7,
      active: true,
      min_volume_usdt: 10000,
      bonus_percent: 1.5,
    },
  ],
}

export const demoTeamleadStats = {
  total_earned_usdt: 21800,
  orders_count: 1840,
  active_links_count: 5,
}

export const demoTeamleadLinks = [
  {
    id: 701,
    linked_entity_type: 'merchant',
    linked_entity_id: 101,
    login: 'Закрытая медиа-группа',
    fee_percent: 0.4,
    payout_fee_percent: 0.2,
    is_active: true,
    income_usdt: 9200,
    created_at: '2026-07-04T09:00:00Z',
  },
  {
    id: 702,
    linked_entity_type: 'trader',
    linked_entity_id: 301,
    login: 'трейдер_север',
    fee_percent: 0.35,
    payout_fee_percent: 0.15,
    is_active: true,
    income_usdt: 7600,
    created_at: '2026-07-05T10:20:00Z',
  },
  {
    id: 703,
    linked_entity_type: 'merchant',
    linked_entity_id: 102,
    login: 'Кабинет авторов',
    fee_percent: 0.25,
    payout_fee_percent: 0.1,
    is_active: true,
    income_usdt: 5000,
    created_at: '2026-07-06T14:15:00Z',
  },
]

export const demoTeamleadRewards = [
  {
    id: 801,
    amount: 420,
    currency: 'USDT',
    reference_type: 'order',
    reference_id: 'sp-demo-7301',
    description: 'Вознаграждение по закрытой сделке',
    created_at: new Date(now - 28 * 60 * 1000).toISOString(),
  },
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
