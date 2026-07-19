import type {
  Currency,
  DisputeReason,
  DisputeStatus,
  DisputeSubstatus,
  MerchantStatus,
  OrderBookSide,
  OrderStatus,
  PaymentDirection,
  PaymentMethod,
  RateSource,
  RequisiteStatus,
  TraderStatus,
  UserRole,
  WithdrawalStatus,
} from '@/types'

export interface Option<T extends string = string> {
  value: T | ''
  label: string
}

const ALL: Option = { value: '', label: 'Все' }

// ─── Payment methods ────────────────────────────────────────
export const ALL_PAYMENT_METHODS: PaymentMethod[] = ['sbp', 'card', 'sim']

export const paymentMethodLabels: Record<PaymentMethod, string> = {
  sbp: 'SBP',
  card: 'CARD',
  sim: 'SIM',
}

export const paymentMethodLabelsRu: Record<PaymentMethod, string> = {
  sbp: 'СБП',
  card: 'Карта',
  sim: 'SIM',
}

export const paymentMethodOptions: Option<PaymentMethod>[] = ALL_PAYMENT_METHODS.map(m => ({
  value: m,
  label: paymentMethodLabels[m],
}))

export const paymentMethodOptionsWithAll: Option[] = [ALL, ...paymentMethodOptions]

export const paymentMethodOptionsRu: Option<PaymentMethod>[] = ALL_PAYMENT_METHODS.map(m => ({
  value: m,
  label: paymentMethodLabelsRu[m],
}))

export const paymentMethodOptionsRuWithAll: Option[] = [ALL, ...paymentMethodOptionsRu]

// ─── Order statuses ─────────────────────────────────────────
export const orderStatusLabels: Record<OrderStatus, string> = {
  created: 'Создана',
  pending: 'Ожидание',
  receipt_uploaded: 'Чек загружен',
  success: 'Успешно',
  disputed: 'Спор',
  canceled: 'Отменена',
  failed: 'Неудача',
  refunded: 'Возврат',
}

export const orderStatusOptions: Option<OrderStatus>[] = (Object.keys(orderStatusLabels) as OrderStatus[]).map(s => ({
  value: s,
  label: orderStatusLabels[s],
}))

export const orderStatusOptionsWithAll: Option[] = [ALL, ...orderStatusOptions]

// ─── Payout statuses ────────────────────────────────────────
export const payoutStatusLabels: Record<string, string> = {
  created: 'Создана',
  claimed: 'Взято',
  awaiting_check: 'На проверке',
  completed: 'Выполнено',
  canceled: 'Отменена',
  expired: 'Истекло',
}

export const payoutStatusOptions: Option[] = Object.keys(payoutStatusLabels).map(s => ({
  value: s,
  label: payoutStatusLabels[s],
}))

export const payoutStatusOptionsWithAll: Option[] = [ALL, ...payoutStatusOptions]

// ─── Dispute statuses ───────────────────────────────────────
export const disputeStatusLabels: Record<DisputeStatus, string> = {
  open: 'Открыт',
  resolved: 'Решён',
  rejected: 'Отклонён',
}

export const disputeStatusOptions: Option<DisputeStatus>[] = (Object.keys(disputeStatusLabels) as DisputeStatus[]).map(s => ({
  value: s,
  label: disputeStatusLabels[s],
}))

export const disputeStatusOptionsWithAll: Option[] = [ALL, ...disputeStatusOptions]

// ─── Dispute reasons ────────────────────────────────────────
export const disputeReasonLabels: Record<DisputeReason, string> = {
  unknown: 'Неизвестная причина спора',
  has_payment: 'Платеж был произведен',
  no_payment: 'Платеж не был произведен',
  invalid_sum: 'Неверная сумма',
  invalid_requisites: 'Неверные реквизиты',
  premoderation: 'Премодерация чека',
  check_suspended: 'Запрошен PDF/видео',
}

// ``premoderation`` / ``check_suspended`` are system-only (opened automatically
// by receipt review / a trader-admin PDF-video request) — valid labels/filters
// but never manually selectable reasons.
export const disputeReasonOptions: Option<DisputeReason>[] = (Object.keys(disputeReasonLabels) as DisputeReason[])
  .filter(r => r !== 'premoderation' && r !== 'check_suspended')
  .map(r => ({
    value: r,
    label: disputeReasonLabels[r],
  }))

export const disputeReasonOptionsWithAll: Option[] = [ALL, ...disputeReasonOptions]

// ─── Dispute substatus (premoderation context) ──────────────
export const disputeSubstatusLabels: Record<DisputeSubstatus, string> = {
  pdf_requested: 'Запрошен PDF чека',
  video_requested: 'Запрошено видео-подтверждение',
}

// ─── Merchant (terminal) statuses ───────────────────────────
export const merchantStatusLabels: Record<MerchantStatus, string> = {
  pending: 'На модерации',
  test: 'Тест',
  enabled: 'Активен',
  disabled: 'Выключен',
  blocked: 'Заблокирован',
  archived: 'Архив',
}

export const merchantStatusOptions: Option<MerchantStatus>[] = (Object.keys(merchantStatusLabels) as MerchantStatus[]).map(s => ({
  value: s,
  label: merchantStatusLabels[s],
}))

export const merchantStatusOptionsWithAll: Option[] = [ALL, ...merchantStatusOptions]

// ─── Trader statuses ────────────────────────────────────────
export const traderStatusLabels: Record<TraderStatus, string> = {
  enabled: 'Активен',
  disabled: 'Выключен',
  blocked: 'Заблокирован',
}

export const traderStatusOptions: Option<TraderStatus>[] = (Object.keys(traderStatusLabels) as TraderStatus[]).map(s => ({
  value: s,
  label: traderStatusLabels[s],
}))

export const traderStatusOptionsWithAll: Option[] = [ALL, ...traderStatusOptions]

// ─── Requisite statuses ─────────────────────────────────────
export const requisiteStatusLabels: Record<RequisiteStatus, string> = {
  enabled: 'Активен',
  disabled: 'Выключен',
  blocked: 'Заблокирован',
  archived: 'Архив',
}

export const requisiteStatusOptions: Option<RequisiteStatus>[] = (Object.keys(requisiteStatusLabels) as RequisiteStatus[]).map(s => ({
  value: s,
  label: requisiteStatusLabels[s],
}))

export const requisiteStatusOptionsWithAll: Option[] = [ALL, ...requisiteStatusOptions]

// ─── Withdrawal statuses ────────────────────────────────────
export const withdrawalStatusLabels: Record<WithdrawalStatus, string> = {
  pending: 'Ожидание',
  success: 'Выполнен',
  rejected: 'Отклонён',
}

export const withdrawalStatusOptions: Option<WithdrawalStatus>[] = (Object.keys(withdrawalStatusLabels) as WithdrawalStatus[]).map(s => ({
  value: s,
  label: withdrawalStatusLabels[s],
}))

export const withdrawalStatusOptionsWithAll: Option[] = [ALL, ...withdrawalStatusOptions]

// ─── User roles ─────────────────────────────────────────────
export const userRoleLabels: Record<UserRole, string> = {
  admin: 'Админ',
  support: 'Саппорт',
  merchant: 'Мерчант',
  trader: 'Трейдер',
  teamlead: 'Тимлид',
}

export const userRoleOptions: Option<UserRole>[] = (Object.keys(userRoleLabels) as UserRole[]).map(r => ({
  value: r,
  label: userRoleLabels[r],
}))

export const userRoleOptionsWithAll: Option[] = [ALL, ...userRoleOptions]

// ─── Currency ───────────────────────────────────────────────
export const ALL_CURRENCIES: Currency[] = ['RUB', 'AZN', 'USDT']
export const CREATE_ORDER_CURRENCIES: Currency[] = ['RUB', 'AZN']

export const currencyOptions: Option<Currency>[] = ALL_CURRENCIES.map(c => ({
  value: c,
  label: c,
}))

export const createOrderCurrencyOptions: Option<Currency>[] = CREATE_ORDER_CURRENCIES.map(c => ({
  value: c,
  label: c,
}))

// ─── Requisite trader priority (1–3 weight, resolver-side) ──
// Not `Option<T>` — that generic is constrained to `T extends string`, and
// this is a numeric BaseSelect (see BaseSelect's `SelectOption.value: string | number`).
export const requisitePriorityOptions: Array<{ value: number; label: string }> = [
  { value: 1, label: '1' },
  { value: 2, label: '2' },
  { value: 3, label: '3' },
]

// ─── Payment direction ──────────────────────────────────────
export const paymentDirectionLabels: Record<PaymentDirection, string> = {
  payin: 'Payin',
  payout: 'Payout',
}

export const paymentDirectionOptions: Option<PaymentDirection>[] = (Object.keys(paymentDirectionLabels) as PaymentDirection[]).map(d => ({
  value: d,
  label: paymentDirectionLabels[d],
}))

export const paymentDirectionOptionsWithAll: Option[] = [ALL, ...paymentDirectionOptions]

// ─── Rates ──────────────────────────────────────────────────
export const rateSourceLabels: Record<RateSource, string> = {
  bybit: 'Bybit',
  rapira: 'Rapira',
}

export const rateSourceOptions: Option<RateSource>[] = (Object.keys(rateSourceLabels) as RateSource[]).map(s => ({
  value: s,
  label: rateSourceLabels[s],
}))

export const orderBookSideLabels: Record<OrderBookSide, string> = {
  buy: 'Buy',
  sell: 'Sell',
}

export const orderBookSideOptions: Option<OrderBookSide>[] = (Object.keys(orderBookSideLabels) as OrderBookSide[]).map(s => ({
  value: s,
  label: orderBookSideLabels[s],
}))

// ─── Tristate (true/false/all) ──────────────────────────────
export const tristateOptions: Array<{ value: string; label: string }> = [
  { value: '', label: 'Все' },
  { value: 'true', label: 'Да' },
  { value: 'false', label: 'Нет' },
]
