// ─── Generic ─────────────────────────────────────────────

/** Paginated list envelope returned by list endpoints: rows + the total row
 *  count, so the UI can render exact page counts (mirrors the backend). */
export interface Paginated<T> {
  items: T[]
  total: number
}

// ─── Enums ───────────────────────────────────────────────

export type UserRole = 'admin' | 'merchant' | 'trader' | 'teamlead'

export type OrderStatus =
  | 'created'
  | 'pending'
  | 'receipt_uploaded'
  | 'success'
  | 'disputed'
  | 'canceled'
  | 'failed'
  | 'refunded'

export type PaymentDirection = 'payin' | 'payout'
export type PaymentMethod = 'sbp' | 'card' | 'sim'
export type Currency = 'RUB' | 'AZN' | 'USDT'
export type WithdrawalStatus = 'pending' | 'success' | 'rejected'
export type MerchantStatus = 'pending' | 'test' | 'enabled' | 'disabled' | 'blocked' | 'archived'
export type TraderStatus = 'enabled' | 'disabled' | 'blocked'
export type RequisiteStatus = 'enabled' | 'disabled' | 'blocked' | 'archived'
export type DisputeStatus = 'open' | 'resolved' | 'rejected'
export type DisputeReason = 'unknown' | 'has_payment' | 'no_payment' | 'invalid_sum' | 'invalid_requisites' | 'premoderation' | 'check_suspended'
export type DisputeSubstatus = 'pdf_requested' | 'video_requested'
export type RateSource = 'bybit' | 'rapira'
export type OrderBookSide = 'buy' | 'sell'

// ─── Users ───────────────────────────────────────────────

export interface User {
  id: number
  username: string
  role: UserRole
  totp_enabled: boolean
  is_blocked: boolean
  use_shared_balance: boolean
  timezone?: string | null
  created_at: string
  balance_usdt?: number
}

export interface UserUpdate {
  is_blocked?: boolean
  use_shared_balance?: boolean
}

export interface UserMeUpdate {
  timezone?: string | null
}

export interface TokenResponse {
  access_token: string
  refresh_token: string
  token_type: string
  user: User
}

export interface Setup2FAResponse {
  secret: string
  provisioning_uri: string
}

// ─── Traders ─────────────────────────────────────────────

export interface TraderMethodConfig {
  fee: number
  min_amount: number
  max_amount: number
  is_active?: boolean
}

export interface TraderGroupBrief {
  id: number
  name: string
  description?: string
}

export interface TraderGroupMemberBrief {
  id: number
  user_id: number
}

export interface TraderGroupMerchantBrief {
  id: number
  name?: string | null
}

export interface TraderGroup extends TraderGroupBrief {
  traders?: TraderGroupMemberBrief[]
  merchants?: TraderGroupMerchantBrief[]
}

export interface TraderMerchantBrief {
  id: number
  name?: string | null
}

export interface Trader {
  id: number
  user_id: number
  status: TraderStatus
  is_payin_active: boolean
  is_payout_active: boolean
  telegram_group_id?: number | null
  accept_all_merchants?: boolean
  receipt_auto_check?: boolean
  default_receipt_check_provider_id?: number | null
  methods_config: Record<PaymentMethod, TraderMethodConfig>
  achievement_bonus_percent?: number
  priority_bonus_percent?: number
  groups: TraderGroupBrief[]
  merchants?: TraderMerchantBrief[]
}

export interface TraderAchievementItem {
  rule_type: string
  level_key?: string | null
  bonus_percent: number
}

export interface TraderTierLevel {
  threshold_usdt: number // порог среднего оборота
  percent: number
}

export interface TraderTierProgress {
  window_days: number // X — среднее считается за столько дней (= длина стрика)
  avg_volume_usdt: number
  current_index: number // 0-based reached level; -1 if below the first
  current_percent: number
  locked: boolean // true, пока стрик не набран → бонус уровня не активен
  levels: TraderTierLevel[] // ascending by threshold
}

export interface TraderStreakProgress {
  current_days: number
  target_days: number // X — сколько дней подряд нужно, чтобы разблокировать бонус
  active: boolean
  min_volume_usdt: number
  bonus_percent: number // бонус уровня, который разблокирует стрик
}

export interface TraderAchievements {
  enabled: boolean
  total_bonus_percent: number
  today_volume_usdt: number
  items: TraderAchievementItem[]
  tiers: TraderTierProgress[]
  streaks: TraderStreakProgress[]
}

// ── Достижения / бонусы — админ-конфиг (platform settings) ─────────────────
// Единая модель: стрик-гейт + уровень по среднему обороту за X дней.
export interface AchievementTier {
  min_avg: number // нижняя граница полосы среднего оборота (USDT)
  percent: number // бонус к ставке (п.п.)
}

export interface AchievementRule {
  type: 'streak_volume_tier'
  streak_days: number // X: дней подряд нужно удержать
  min_daily_volume: number // день засчитан в стрик, если оборот ≥ этого
  tiers: AchievementTier[]
}

export interface AchievementSettings {
  enabled: boolean
  bonus_max_percent: number
  rules: AchievementRule[]
}

export interface AchievementSettingsUpdate {
  enabled?: boolean
  bonus_max_percent?: number
  rules?: AchievementRule[]
}

export interface TraderUpdateAdmin {
  status?: TraderStatus
  is_payin_active?: boolean
  is_payout_active?: boolean
  telegram_group_id?: number | null
  methods_config?: Record<PaymentMethod, TraderMethodConfig>
  merchant_ids?: number[]
  group_ids?: number[]
  accept_all_merchants?: boolean
  priority_bonus_percent?: number
}

// ─── Orders ──────────────────────────────────────────────

export interface OrderRequisiteInfo {
  id?: number | null
  nickname?: string | null
  bank_name: string
  account_number: string
  account_holder: string
  payment_method: PaymentMethod
  currency: Currency
  payment_option_id?: number | null
  payment_option_code?: string | null
  payment_option_name?: string | null
  logo_url?: string | null
}

export interface Order {
  id: number
  uuid: string
  external_id: string
  merchant_id: number
  merchant_name?: string | null
  trader_id?: number
  requisite_id?: number
  provider_order_id?: string | null
  client_user_id?: string | null  // merchant's clientID — admin-only
  direction: PaymentDirection
  amount: number
  currency: Currency
  payment_method: PaymentMethod
  amount_usdt?: number
  exchange_rate?: number
  fee_usdt?: number
  trader_fee_usdt?: number
  profit_usdt?: number
  // Denormalized financial snapshot (admin-only; platform-internal).
  teamlead_reward_usdt?: number | null
  platform_profit_usdt?: number | null
  financials?: OrderFinancials | null
  status: OrderStatus
  receipt_file?: string
  receipt_uploaded_at?: string
  created_at: string
  updated_at: string
  date_end?: string
  confirmed_at?: string
  rejected_at?: string
  rejection_reason?: string
  webhook_url?: string
  requisite?: OrderRequisiteInfo | null
}

// A unique merchant client (the merchant's clientID / userId). Admin-only.
export interface Client {
  id: number
  merchant_id: number
  merchant_name?: string | null
  client_user_id: string
  is_blocked: boolean
  block_reason?: string | null
  blocked_by_admin_id?: number | null
  blocked_at?: string | null
  first_seen_at?: string | null
  last_seen_at?: string | null
  total_orders: number
  successful_orders: number
  turnover_usdt: number       // Σ amount_usdt of successful orders
  conversion: number          // successful / total, 0..1 (derived server-side)
  blocked_attempts: number    // withheld requisites while the client was blocked
  created_at: string
  updated_at: string
}

// Compact client block for the admin order modal — our internal public_id plus
// all-time deals/conversion. Served only when the order's merchant has unique
// clients enabled (otherwise the endpoint returns null).
export interface OrderClientInfo {
  public_id: string
  total_orders: number
  successful_orders: number
  turnover_usdt: number       // Σ amount_usdt of the client's successful orders
  conversion: number          // successful / total, 0..1 (derived server-side)
}

// Restricted client block for the TRADER order modal — internal public_id +
// all-time turnover/conversion (no raw order counts). Served only for the
// trader's own order when the merchant has unique clients enabled.
export interface TraderOrderClientInfo {
  public_id: string
  turnover_usdt: number
  conversion: number
}

/** One teamlead's reward on a settled order (from `Order.financials`). */
export interface TeamleadRewardBreakdown {
  teamlead_id: number
  side: 'merchant' | 'trader'
  fee_percent: number
  reward_usdt: string
}

/** Denormalized financial snapshot projected onto the order by the backend. */
export interface OrderFinancials {
  amount_usdt: string
  fee_usdt: string
  trader_fee_usdt: string
  merchant_net_usdt: string
  teamlead_reward_usdt: string
  platform_profit_usdt: string
  teamlead_rewards: TeamleadRewardBreakdown[]
  settled: boolean
  status: string
}

export interface OrderStatusHistory {
  id: number
  order_id: number
  old_status?: OrderStatus
  new_status: OrderStatus
  changed_by_user_id?: number
  reason?: string
  created_at: string
}

export interface CallbackAttempt {
  id: number
  order_id: number
  url: string
  request_headers?: Record<string, any>
  request_payload: Record<string, any>
  response_status?: number
  response_headers?: Record<string, any>
  response_body?: string
  is_successful: boolean
  attempt_number: number
  created_at: string
}

export interface MerchantApiLog {
  request_id?: string | null
  merchant_id?: number | null
  order_id?: number | null
  url: string
  method: string
  request_headers?: Record<string, any> | null
  request_body?: string | null
  response_status?: number | null
  response_headers?: Record<string, any> | null
  response_body?: string | null
  response_time_ms?: number | null
  created_at: string
}

export interface OrderCreationSnapshot {
  request_id?: string | null
  merchant_id?: number | null
  order_id?: number | null
  response_time_ms?: number | null
  request_data: Record<string, any>
  merchant_snapshot: Record<string, any>
  rate_snapshot?: Record<string, any> | null
  traders_snapshot: Record<string, any>[]
  candidates: Record<string, any>[]
  result: Record<string, any>
  created_at: string
}

export interface CallbackAttempt {
  id: number
  order_id: number
  url: string
  request_headers?: Record<string, any>
  request_payload: Record<string, any>
  response_status?: number
  response_headers?: Record<string, any>
  response_body?: string
  is_successful: boolean
  attempt_number: number
  created_at: string
}

// Inbound provider → us callback. One row per POST to
// /api/cascade/v1/callbacks/{code}, persisted regardless of outcome.
// Inbound provider → us callback (ClickHouse-backed; no row id).
export interface ProviderCallbackAttempt {
  provider_id?: number | null
  provider_code: string
  order_id?: number | null
  external_order_id?: string | null
  request_headers?: Record<string, any> | null
  request_body?: string | null
  signature_valid: boolean
  parsed_status?: string | null
  response_status?: number | null
  response_body?: string | null
  error_message?: string | null
  processing_ms?: number
  request_id?: string | null
  created_at: string
}

// Outbound provider HTTP request log (ClickHouse-backed).
export interface ProviderRequestLog {
  ts: string
  request_id: string
  provider_id: string
  provider_code: string
  order_id: string
  method: string
  url: string
  request_headers: string
  request_body: string
  response_status: number
  response_body: string
  success: boolean
  error: string
  provider_latency_ms: number
  e2e_ms: number
  request_type: string
}

export interface BalanceRefInfo {
  id: number
  type: BalanceType
  currency: Currency
  /**
   * Narrowest role we can derive for the balance owner:
   * - `merchant` for merchant-backed balances
   * - `trader` / `teamlead` / `admin` for user-backed balances (from `users.role`)
   * - `system` for the platform pool
   * - `user` as a fallback if the role can't be resolved
   */
  owner_kind: 'merchant' | 'trader' | 'teamlead' | 'admin' | 'user' | 'system'
  owner_id?: number | null
  /** Username for users, merchant name for merchants, "Система" for system. */
  owner_label: string
}

export interface LedgerEntry {
  id: number
  from_balance_id?: number
  to_balance_id?: number
  from_balance?: BalanceRefInfo | null
  to_balance?: BalanceRefInfo | null
  amount: number
  currency: Currency
  reference_type: string
  reference_id: string
  description?: string
  created_at: string
}

export interface DisputeResponse {
  id: number
  uuid: string
  order_id: number
  merchant_id: number
  initiator_type: UserRole
  initiator_id?: number
  status: DisputeStatus
  substatus?: DisputeSubstatus | null  // premoderation context (pdf/video requested)
  reason: DisputeReason
  evidence_files?: string[]            // admin only — internal receipt paths
  evidence_count?: number              // merchant / trader — # of attached files
  assigned_user_type?: UserRole
  assigned_user_id?: number
  resolution_text?: string
  resolved_by_type?: UserRole
  resolved_by_id?: number
  resolved_at?: string
  created_at?: string
  trader_login?: string | null
  merchant_login?: string | null
  order_uuid?: string | null
  order_external_id?: string | null
  order_amount?: number | null
  order_payment_method?: PaymentMethod | string | null
}

// One dispute-evidence file (mirrors backend ReceiptItem). The bytes are
// fetched separately by uuid from the role-scoped download endpoint.
export interface DisputeEvidenceItem {
  uuid: string
  created_at: string
  source: string
  filename: string
}

export interface TraderCandidateInfo {
  trader_id?: number | null
  user_id?: number | null
  username?: string | null
  requisite_id?: number | null
  is_selected: boolean
  is_excluded: boolean
  reason?: string | null
  status?: string | null
  is_payin_active?: boolean | null
  is_payout_active?: boolean | null
  method_config?: Record<string, any> | null
}

export interface OrderDebug {
  order: Order
  status_history: OrderStatusHistory[]
  merchant_api_logs: MerchantApiLog[]
  callback_attempts: CallbackAttempt[]
  ledger_entries: LedgerEntry[]
  dispute?: DisputeResponse
  traders_candidates?: TraderCandidateInfo[]
}

// ─── Requisites ──────────────────────────────────────────

export interface RequisiteLimit {
  id: number
  requisite_id: number
  limit_daily: number
  limit_monthly: number
  limit_min_transaction: number
  limit_max_transaction: number
  limit_max_concurrent_orders?: number
  current_daily_turnover: number
  current_monthly_turnover: number
  reset_enabled?: boolean
  last_reset_at?: string | null
  /** Sum of `amount` for in-flight orders (pending / receipt / disputed). */
  active_amount?: number
  updated_at?: string
}

export interface Requisite {
  id: number
  trader_id: number
  trader_login?: string | null
  nickname?: string | null
  bank_name: string
  account_number: string
  account_holder: string
  payment_method: PaymentMethod
  currency: Currency
  payment_option_id?: number | null
  payment_option?: PaymentOption | null
  status: RequisiteStatus
  is_active: boolean
  is_archived: boolean
  last_used_at?: string
  limits?: RequisiteLimit
  trader_priority: number
}

export interface RequisiteLimitsPayload {
  limit_daily?: number
  limit_monthly?: number
  limit_min_transaction?: number
  limit_max_transaction?: number
  limit_max_concurrent_orders?: number
  reset_enabled?: boolean
}

export interface RequisiteCreate {
  nickname?: string | null
  payment_option_id: number
  account_number: string
  account_holder: string
  payment_method: PaymentMethod
  currency?: Currency
  limits?: RequisiteLimitsPayload
  trader_priority?: number
}

export interface RequisiteUpdate {
  nickname?: string | null
  payment_option_id?: number
  account_number?: string
  account_holder?: string
  payment_method?: PaymentMethod
  status?: RequisiteStatus
  is_active?: boolean
  is_archived?: boolean
  limits?: RequisiteLimitsPayload
  trader_priority?: number
}

// ─── Disputes ────────────────────────────────────────────

export interface DisputeResolution {
  resolution_text: string
}

// ─── Finance ─────────────────────────────────────────────

export type BalanceType = 'work' | 'escrow' | 'safe_deposit'

export interface BalanceInfo {
  id: number
  user_id: number | null
  merchant_id: number | null
  is_system: boolean
  type: BalanceType
  currency: Currency
  amount: number
}

export interface AdminAdjustRequest {
  user_id?: number | null
  merchant_id?: number | null
  /** Positive = credit, negative = debit */
  amount: number
  currency: Currency
  balance_type: BalanceType
  reason: string
}

export interface AdminAdjustResponse {
  ledger_entry_id: number
  balance_id: number
  amount: number
  currency: Currency
  balance_type: BalanceType
  entity: string
}

/** Preview of a TRC20 (USDT) deposit parsed on-chain — no credit yet. */
export interface HashDepositPreview {
  user_id: number
  trader_login?: string | null
  tx_hash: string
  amount: number
  currency: string
  to_address: string
  from_address: string
}

/** Result of confirming a hash deposit — the trader was credited. */
export interface HashDepositResult {
  ledger_entry_id: number
  user_id: number
  amount: number
  currency: string
  tx_hash: string
}

export interface TraderFinanceStatsOrder {
  order_id: number
  order_date: string
  amount_usdt: number
  profit_usdt: number
}

export interface TraderFinanceStats {
  processed_usdt: number
  profit_usdt: number
  orders: TraderFinanceStatsOrder[]
}

export interface WithdrawalRequest {
  id: number
  uuid: string
  user_role: UserRole
  user_id: number
  user_login?: string | null
  merchant_id?: number | null
  merchant_name?: string | null
  amount: number
  currency: Currency
  destination_address: string
  fee_amount: number
  status: WithdrawalStatus
  created_at: string
  updated_at: string
  processed_at?: string
  processed_by_id?: number
  rejection_reason?: string
}

// ─── Rates ───────────────────────────────────────────────

export interface RateConfig {
  id: number
  name: string
  source: RateSource
  side: OrderBookSide
  position: number
  payment_methods: string[]
  fiat_currency: Currency
  crypto_currency: string
  update_interval_seconds: number
  is_active: boolean
  current_rate?: number
  last_updated_at?: string
}

export interface RateConfigCreate {
  name: string
  source?: RateSource
  side: OrderBookSide
  position?: number
  payment_methods?: string[]
  fiat_currency: Currency
  crypto_currency?: string
  update_interval_seconds?: number
  is_active?: boolean
}

export interface RateConfigUpdate {
  name?: string
  side?: OrderBookSide
  position?: number
  payment_methods?: string[]
  update_interval_seconds?: number
  is_active?: boolean
}

// ─── Audit ───────────────────────────────────────────────

export interface AuditLog {
  id: number
  user_id?: string
  action: string
  entity_type: string
  entity_id: string
  old_values?: Record<string, any>
  new_values?: Record<string, any>
  request_id?: string
  created_at: string
}

// ─── Merchant ────────────────────────────────────────────

export interface PaymentOption {
  id: number
  code: string
  name: string
  logo_url?: string
  supported_methods: string[]
  currency: Currency
  is_active: boolean
}

export interface PaymentMethodInfo {
  method: PaymentMethod
  fee_percentage: number
}

export interface TelegramUser {
  id: number
  label?: string | null
  active?: boolean
}

export interface MerchantTraderBrief {
  id: number
  user_id: number
}

export interface MerchantTraderGroupBrief {
  id: number
  name: string
}

export interface MerchantAdmin {
  id: number
  user_id: number
  name?: string
  status: MerchantStatus
  currency: Currency
  webhook_url?: string
  order_ttl_seconds: number
  requisite_search_timeout_ms: number
  fees: Record<string, number>
  withdrawal_fee_fixed: number
  rate_config_id?: number | null
  telegram_user_ids: TelegramUser[]
  traders?: MerchantTraderBrief[]
  trader_groups?: MerchantTraderGroupBrief[]
  cascade_mode?: CascadeMode
  cascade_group_ids?: number[]
  receipt_premoderation_enabled?: boolean | null
  notify_telegram_group_id?: number | null
  dispute_telegram_group_id?: number | null
  dispute_id_mask?: string | null
  unique_clients_enabled?: boolean
  proof_request_notify_enabled?: boolean
}

// ── Cascade ─────────────────────────────────────────
export type CascadeMode = 'off' | 'grouped' | 'pooled'
export type CascadeRateSource = 'provider' | 'platform'
export type CascadeAttemptStatus =
  | 'in_flight'
  | 'won'
  | 'lost'
  | 'cancelled'
  | 'refused'
  | 'timeout'
  | 'error'

export type AdapterFieldType =
  | 'string'
  | 'textarea'
  | 'url'
  | 'number'
  | 'boolean'
  | 'select'
  | 'multi_select'
  | 'kv_map'

export interface AdapterFieldOption {
  value: string
  label: string
}

export interface AdapterFieldSpec {
  key: string
  label: string
  type: AdapterFieldType
  required?: boolean
  default?: unknown
  description?: string
  placeholder?: string
  secret?: boolean
  options?: AdapterFieldOption[]
  value_type?: 'string' | 'number' | 'boolean' | 'select'
  min?: number | null
  max?: number | null
}

export interface CascadeAdapterInfo {
  code: string
  display_name: string
  description: string
  supports_provider_rate: boolean
  settings_schema: AdapterFieldSpec[]
  supported_methods: string[]
  // Per-adapter credentials form — drives the "Аутентификация" tab.
  // Same AdapterFieldSpec shape as ``settings_schema``; ``key`` is one of
  // ``api_key`` / ``api_secret`` / ``webhook_secret``.
  credentials_schema: AdapterFieldSpec[]
  // Filename in ``public/logos/`` (e.g. ``"swifty.svg"``) or empty string
  // when the adapter ships without a logo. Combined with the ``/logos/``
  // prefix by the admin UI to render a small icon badge.
  logo_filename: string
}

export interface CascadeProvider {
  id: number
  code: string
  name: string
  adapter_type: string
  is_active: boolean
  base_url: string
  fees: Record<string, number>
  rate_source: CascadeRateSource
  rate_config_id?: number | null
  min_amount_fiat?: number | null
  max_amount_fiat?: number | null
  cb_window_seconds: number
  cb_threshold_failures: number
  cb_threshold_rate: number
  cb_cooldown_seconds: number
  disabled_until?: string | null
  request_timeout_ms: number
  cancel_timeout_ms: number
  priority_weight: number
  settings: Record<string, unknown>
  virtual_user_id: number
  virtual_trader_id: number
  created_at: string
  updated_at: string
}

export interface CascadeProviderCreate {
  code: string
  name: string
  adapter_type: string
  is_active?: boolean
  base_url: string
  api_key?: string
  api_secret?: string
  webhook_secret?: string
  fees?: Record<string, number>
  rate_source?: CascadeRateSource
  rate_config_id?: number | null
  min_amount_fiat?: number
  max_amount_fiat?: number
  cb_window_seconds?: number
  cb_threshold_failures?: number
  cb_threshold_rate?: number
  cb_cooldown_seconds?: number
  request_timeout_ms?: number
  cancel_timeout_ms?: number
  priority_weight?: number
  settings?: Record<string, unknown>
  initial_balance_usdt?: number
}

export interface CascadeProviderUpdate
  extends Partial<Omit<CascadeProviderCreate, 'code'>> {}

export interface CascadeGroupProviderRef {
  id: number
  code: string
  name: string
  is_active: boolean
}

export interface CascadeGroupMerchantRef {
  id: number
  name?: string | null
}

export interface CascadeGroup {
  id: number
  name: string
  description?: string | null
  tier: number
  timeout_ms: number
  is_active: boolean
  created_at: string
  updated_at: string
  providers: CascadeGroupProviderRef[]
  merchants: CascadeGroupMerchantRef[]
}

export interface CascadeGroupCreate {
  name: string
  description?: string
  tier?: number
  timeout_ms?: number
  is_active?: boolean
  provider_ids?: number[]
  merchant_ids?: number[]
}

export interface CascadeGroupUpdate {
  name?: string
  description?: string | null
  tier?: number
  timeout_ms?: number
  is_active?: boolean
}

export interface CascadeOrderAttempt {
  id: number
  order_id: number
  provider_id: number
  group_id?: number | null
  tier?: number | null
  started_at: string
  finished_at?: string | null
  latency_ms?: number | null
  status: CascadeAttemptStatus
  refusal_reason?: string | null
  error_code?: string | null
  error_message?: string | null
  external_order_id?: string | null
  requisite_id?: number | null
  provider_rate?: number | null
  provider_fee_usdt?: number | null
  our_profit_usdt?: number | null
  idempotency_key: string
  created_at: string
}

export interface CascadeMetricPoint {
  bucket_at: string
  request_count: number
  success_count: number
  failure_count: number
  timeout_count: number
  cancel_count: number
  avg_latency_ms: number
  success_rate: number
  total_volume_usdt: number
  total_profit_usdt: number
}

export interface CascadeProviderMetrics {
  provider_id: number
  points: CascadeMetricPoint[]
  totals: CascadeMetricPoint
}

// Envelope returned by POST /cascade/providers/{id}/test-issue. The
// backend always populates the top-level identity + timing fields; the
// optional sub-objects depend on which branch fired (issued / refusal /
// timeout / error / unsupported). Treat ``outcome`` as the
// discriminator when rendering.
export interface CascadeProviderTestIssueResult {
  ok: boolean
  outcome: 'issued' | 'refusal' | 'timeout' | 'error' | 'unsupported'
  summary: string
  provider_id?: number
  provider_code?: string
  adapter_type?: string
  idempotency_key?: string
  started_at?: string
  finished_at?: string
  latency_ms?: number
  request?: {
    amount: string
    payment_method: string
    payment_option_code?: string | null
  }
  // Present when outcome === 'issued'
  requisite?: {
    external_order_id: string
    bank_name: string
    account_number: string
    account_holder: string
    payment_method: string
    payment_option_code?: string | null
    amount_fiat: string
    provider_rate?: string | null
    expires_at: string
  }
  // Present when outcome === 'refusal'
  refusal_code?: string
  refusal_message?: string
  // Present when outcome === 'issued' and auto_cancel was true
  auto_cancel?: {
    called: boolean
    ok: boolean
    error?: string
  }
  // Snapshot of the exact HTTP request the adapter generated — same
  // pipeline as the live call (build_payin_request → acquire_token →
  // sign_request). Credential-bearing headers are masked (head + length)
  // so screenshots don't leak tokens.
  request_preview?: {
    method?: string
    url?: string
    headers?: Record<string, string>
    body?: unknown
    params?: Record<string, unknown>
    error?: string
  }
  // Full raw response from the provider — useful for debugging
  raw?: unknown
}

export interface MerchantProfile {
  id: number
  status: MerchantStatus
  currency: Currency
  webhook_url?: string
  balance: number
  payment_methods: PaymentMethodInfo[]
}

export interface MerchantFullProfile {
  id: number
  name?: string
  status: MerchantStatus
  currency: Currency
  webhook_url?: string
  api_key_masked: string
  balance_work: number
  balance_escrow: number
  order_ttl_seconds: number
  requisite_search_timeout_ms: number
  fees: Record<string, number>
  withdrawal_fee_fixed: number
  payment_methods: PaymentMethodInfo[]
  telegram_user_ids: TelegramUser[]
}

export interface MerchantSettingsUpdate {
  webhook_url?: string | null
  order_ttl_seconds?: number
  requisite_search_timeout_ms?: number
  name?: string
  telegram_user_ids?: TelegramUser[]
}

export interface MerchantListItem {
  id: number
  name?: string
  status: MerchantStatus
  currency: Currency
  api_key_masked: string
  webhook_url?: string
  order_ttl_seconds: number
}

export interface MerchantCreateResponse {
  id: number
  name?: string
  status: MerchantStatus
  currency: Currency
  api_key: string
  api_secret: string
}

export interface MerchantStats {
  turnover_usdt: number
  fee_usdt: number
  orders_total: number
  orders_success: number
  orders_active: number
  orders_failed: number
  conversion_pct: number
  pending_withdrawals: number
  active_disputes: number
  requests_rub?: number
  payin_requests_total?: number
  payout_pct?: number
}

export interface MerchantPayinCreate {
  amount: number
  currency: Currency
  payment_method: PaymentMethod
  notificationUrl?: string
  internalId?: string
  userId?: string
  payment_option?: number
  issue_requisite_async?: boolean
}

export interface MerchantOrderResponse {
  id: string
  internalId?: string
  amount: number
  currency: Currency
  status: OrderStatus
  payment_method: PaymentMethod
  payment_url: string
  created_at: string
}

// ─── Teamlead ────────────────────────────────────────────

export interface TeamleadLink {
  id?: number
  teamlead_id: number
  linked_entity_type: UserRole
  linked_entity_id: number
  fee_percent: number
  payout_fee_percent: number
  is_active: boolean
  created_at: string
  updated_at: string
}

export interface TeamleadLinkCreate {
  teamlead_id: number
  linked_entity_type: 'merchant' | 'trader'
  linked_entity_id: number
  fee_percent?: number
  payout_fee_percent?: number
  is_active?: boolean
}

export interface TeamleadAdminItem {
  id: number
  username: string
  is_active: boolean
  is_blocked: boolean
  balance_usdt: number
  created_at: string
}

export interface TeamleadReward {
  id: number
  amount: number
  currency: string
  reference_type: string
  reference_id: string
  description?: string
  created_at: string
}

export interface TeamleadLinkEnriched {
  id: number
  linked_entity_type: 'merchant' | 'trader'
  linked_entity_id: number
  login: string
  fee_percent: number
  payout_fee_percent: number
  is_active: boolean
  income_usdt: number
  created_at: string
}


export interface TeamleadTraderOrderFinance {
  order_id: number
  closed_at: string
  amount_usdt: number
  teamlead_profit_usdt: number
  trader_id: number
  trader_login: string
}

export interface TeamleadStats {
  total_earned_usdt: number
  orders_count: number
  active_links_count: number
}

// ─── Receipt-check (anti-fraud) ──────────────────────────

export type ReceiptCheckStatus = 'pending' | 'success' | 'failed' | 'cached'
export type ReceiptCheckTrigger = 'manual' | 'auto'
export type ReceiptCheckProviderAdapter = 'trexo' | 'detectio'

export interface ReceiptCheckProvider {
  id: number
  code: string
  name: string
  adapter_type: ReceiptCheckProviderAdapter
  is_active: boolean
  base_url: string
  api_key_masked: string | null
  price_usdt: number
  request_timeout_ms: number
  settings: Record<string, unknown>
  created_at: string
  updated_at: string
}

export interface ReceiptCheckProviderCreate {
  code: string
  name: string
  adapter_type: ReceiptCheckProviderAdapter
  base_url: string
  api_key: string
  price_usdt: number
  request_timeout_ms?: number
  settings?: Record<string, unknown>
  is_active?: boolean
}

export interface ReceiptCheckProviderUpdate {
  name?: string
  base_url?: string
  api_key?: string
  price_usdt?: number
  request_timeout_ms?: number
  is_active?: boolean
  settings?: Record<string, unknown>
}

export interface ReceiptCheckProviderBalance {
  remaining: number | null
  own: number | null
  gifted: number | null
  total_checks: number | null
  error: string | null
  fetched_at: string
}

export interface ReceiptCheckVerdictItem {
  type: string
  message?: string | null
}

export interface ReceiptCheck {
  id: number
  order_id: number
  provider_id?: number | null
  trader_user_id?: number | null
  trigger: ReceiptCheckTrigger
  status: ReceiptCheckStatus
  is_clean?: boolean | null
  verdict?: ReceiptCheckVerdictItem[] | null
  parsed_data?: Record<string, unknown> | null
  provider_check_id?: string | null
  error_code?: string | null
  error_message?: string | null
  price_usdt: number
  charged: boolean
  refunded: boolean
  created_at: string
  finished_at?: string | null
}

/** Slim provider projection the trader picks from (name + price). */
export interface TraderReceiptProvider {
  id: number
  name: string
  price_usdt: number
}

// ─── Receipt premoderation (support-bot) ─────────────────

export type ModerationStatus =
  | 'none'
  | 'pending'
  | 'approved'
  | 'pdf_requested'
  | 'video_requested'

export type ModerationDecision = 'accept' | 'request_pdf' | 'request_video'

export interface ReceiptModerationItem {
  id: number
  order_id: number
  order_uuid: string
  order_external_id: string
  merchant_id: number
  merchant_name?: string | null
  trader_id?: number | null
  trader_username?: string | null
  moderation_status: ModerationStatus
  has_receipt: boolean
  chat_id: number
  message_id?: number | null
  decision?: ModerationDecision | null
  moderator_tg_id?: number | null
  moderator_username?: string | null
  created_at: string
  decided_at?: string | null
}

export interface ReceiptModerationListResponse {
  items: ReceiptModerationItem[]
  total: number
  skip: number
  limit: number
}

export interface ReceiptModerationListParams {
  decision?: ModerationDecision
  pending_only?: boolean
  merchant_id?: number
  date_from?: string
  date_to?: string
  skip?: number
  limit?: number
}

export interface PremoderationSettings {
  receipt_premoderation_enabled: boolean
  support_bot_chat_id: string
  premoderation_reminder_minutes: number
}

export interface PremoderationSettingsUpdate {
  receipt_premoderation_enabled?: boolean
  support_bot_chat_id?: string
  premoderation_reminder_minutes?: number
}

// ── Долив (requisite refill) ──────────────────────────────────────────────
export interface DolivSettings {
  min_amount: number
  max_amount: number
  price_percent: number
  executor_reward_percent: number
  executor_user_ids: string
}

export interface DolivSettingsUpdate {
  min_amount?: number
  max_amount?: number
  price_percent?: number
  executor_reward_percent?: number
  executor_user_ids?: string
}

export interface DolivCreateRequest {
  requisite_id: number
  amount: number
}

export interface DolivAccess {
  is_executor: boolean
}

export interface DolivLimits {
  min_amount: number
  max_amount: number
  price_percent: number
}

export interface Doliv {
  id: string
  status: PayoutStatus
  amount: number
  currency: Currency
  amount_usdt?: number | null
  exchange_rate?: number | null
  price_usdt?: number | null
  executor_reward_usdt?: number | null
  payment_method: PaymentMethod
  req_holder?: string | null
  req_number?: string | null
  req_extra?: string | null
  logo_url?: string | null
  payment_option_name?: string | null
  has_receipt?: boolean
  refill_order_id?: number | null
  refill_requisite_id?: number | null
  requester_trader_id?: number | null
  executor_trader_id?: number | null
  created_at: string
  claimed_at?: string | null
  claim_expires_at?: string | null
  expires_at?: string | null
  completed_at?: string | null
  canceled_at?: string | null
}

/** Admin «Доливы» row — a долив with resolved trader usernames. */
export interface AdminDoliv extends Doliv {
  requester_username?: string | null
  executor_username?: string | null
}

export interface NotificationSettings {
  notifications_chat_id: string
  notify_withdrawal_requests: boolean
}

export interface NotificationSettingsUpdate {
  notifications_chat_id?: string
  notify_withdrawal_requests?: boolean
}

// ─── Prime-Time ──────────────────────────────────────────

export interface PrimeTimeState {
  active: boolean
  points: number | null
  ends_at: string | null
}

export interface PrimeTimeActivate {
  points: number
  minutes: number
}

// ─── Payouts ─────────────────────────────────────────────

export interface PayoutTerminal {
  id: number
  owner_user_id: number
  name: string
  status: string
  currency: string
  api_key: string
  api_secret?: string // only on create
  rate_config_id?: number | null
  commission_percent: number
  ttl_minutes: number
  receipts_to_close: number
  min_amount?: number | null
  max_amount?: number | null
  webhook_url?: string | null
  work_usdt: number
  escrow_usdt: number
  trader_ids: number[]
  created_at: string
}

export interface PayoutTerminalCreate {
  owner_user_id: number
  name: string
  currency?: string
  rate_config_id?: number | null
  commission_percent?: number
  ttl_minutes?: number
  receipts_to_close?: number
  min_amount?: number | null
  max_amount?: number | null
  webhook_url?: string | null
  trader_ids?: number[]
}

export interface AdminPayout {
  id: string
  external_id: string
  payout_terminal_id?: number | null
  trader_id?: number | null
  client_user_id?: string | null
  payment_method: string
  payment_option_name?: string | null
  amount: number
  currency: string
  amount_usdt?: number | null
  exchange_rate?: number | null
  merchant_fee_usdt?: number | null
  trader_fee_usdt?: number | null
  req_holder?: string | null
  req_number?: string | null
  req_extra?: string | null
  status: string
  rejection_reason?: string | null
  receipt_file?: string | null
  claimed_at?: string | null
  trader_hold_until?: string | null
  hold_released_at?: string | null
  expires_at?: string | null
  created_at: string
  completed_at?: string | null
  canceled_at?: string | null
}

export interface TraderPayoutPoolItem {
  id: string
  amount: number
  currency: string
  payment_method: string
  amount_usdt?: number | null
  payment_option_name?: string | null
  created_at: string
  expires_at?: string | null
}

export interface TraderPayout {
  id: string
  status: string
  amount: number
  currency: string
  payment_method: string
  payment_option_name?: string | null
  amount_usdt?: number | null
  trader_fee_usdt?: number | null
  req_holder?: string | null
  req_number?: string | null
  req_extra?: string | null
  client_user_id?: string | null
  receipt_file?: string | null
  claimed_at?: string | null
  claim_expires_at?: string | null
  trader_hold_until?: string | null
  created_at: string
  expires_at?: string | null
  completed_at?: string | null
}

export type PayoutStatus =
  | 'created'
  | 'claimed'
  | 'awaiting_check'
  | 'completed'
  | 'canceled'
  | 'expired'

export interface MerchantPayout {
  id: string
  external_id: string
  payout_terminal_id: number
  terminal_name?: string | null
  payment_method: PaymentMethod
  amount: number
  currency: string
  amount_usdt?: number | null
  merchant_fee_usdt?: number | null
  status: PayoutStatus
  client_user_id?: string | null
  req_holder?: string | null
  req_number?: string | null
  req_extra?: string | null
  rejection_reason?: string | null
  created_at: string
  expires_at?: string | null
  completed_at?: string | null
  canceled_at?: string | null
}

export interface PayoutReceiptItem {
  id: number
  payout_id: number
  trader_id: number
  amount: number
  file?: string | null
  status: string
  rejection_reason?: string | null
  created_at: string
  moderated_at?: string | null
}

// ─── Utility ─────────────────────────────────────────────

export interface PaginationParams {
  skip?: number
  limit?: number
}
