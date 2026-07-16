<template>
  <div>
    <PageHeader title="Финансы">
      <template #actions>
        <BaseButton variant="gold" @click="openRequest">
          <Plus class="mr-1.5 h-4 w-4" />
          Запрос на вывод
        </BaseButton>
      </template>
    </PageHeader>

    <div class="mb-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      <BaseCard v-for="b in balances" :key="b.id" class="p-4">
        <div class="mb-1 text-xs uppercase text-text-muted">{{ balanceTypeLabel(b.type) }} · {{ b.currency }}</div>
        <div class="text-xl font-bold text-text-main">{{ formatAmount(b.amount) }}</div>
      </BaseCard>

      <BaseCard class="p-4 sm:col-span-2 xl:col-span-1">
        <div class="mb-3 flex items-start justify-between gap-3">
          <div>
            <div class="mb-1 text-xs uppercase text-text-muted">Статистика · USDT</div>
            <div class="text-xl font-bold text-text-main">{{ formatAmount(stats?.processed_usdt || 0) }}</div>
            <div class="mt-1 text-xs text-text-muted">Прибыль: {{ formatAmount(stats?.profit_usdt || 0) }} USDT</div>
          </div>
          <BaseButton variant="dark" size="sm" :disabled="!stats?.orders.length" @click="exportStats">
            <Download class="h-4 w-4" />
          </BaseButton>
        </div>

        <div class="grid gap-2 sm:grid-cols-2">
          <BaseInput v-model="statsFilters.date_from" type="date" label="С" />
          <BaseInput v-model="statsFilters.date_to" type="date" label="По" />
        </div>
        <BaseButton class="mt-3 w-full" variant="gold" size="sm" :loading="statsLoading" @click="loadStats">
          Обновить
        </BaseButton>
      </BaseCard>
    </div>

    <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
      <BaseTabs v-model="activeTab" :tabs="tabs" />
      <BaseFilter
        v-if="activeTab === 'ledger'"
        :active-count="ledgerActiveFilters"
        @apply="ledgerPage = 1; loadLedger()"
        @reset="resetLedgerFilters"
      >
        <BaseSelect v-model="ledgerFilters.reference_type" label="Тип операции" :options="ledgerTypeOptions" />
        <BaseInput v-model="ledgerFilters.order_search" label="Ордер" placeholder="ID / UUID / external" />
        <div class="grid grid-cols-2 gap-2">
          <BaseInput v-model="ledgerFilters.amount_from" label="Сумма от" type="number" placeholder="0" />
          <BaseInput v-model="ledgerFilters.amount_to" label="Сумма до" type="number" placeholder="∞" />
        </div>
      </BaseFilter>
      <BaseFilter
        v-else
        :active-count="withdrawalFilters.status ? 1 : 0"
        @apply="withdrawalPage = 1; loadWithdrawals()"
        @reset="resetWithdrawalFilters"
      >
        <BaseSelect v-model="withdrawalFilters.status" label="Статус" :options="statusOptions" />
      </BaseFilter>
    </div>

    <DataTable
      v-if="activeTab === 'ledger'"
      :columns="ledgerColumns"
      :rows="ledgerEntries"
      :loading="ledgerLoading"
      row-key="id"
      :current-page="ledgerPage"
      :total-pages="ledgerTotalPages"
      :per-page="perPage"
      @page-change="p => { ledgerPage = p; loadLedger() }"
      @per-page-change="n => { perPage = n; ledgerPage = 1; loadLedger() }"
    >
      <template #cell-reference_type="{ value }">
        <BaseBadge :color="typeColor(value as string)">{{ formatType(value as string) }}</BaseBadge>
      </template>

      <template #cell-amount="{ row }">
        <span :class="amountClass(row as LedgerEntry)" class="font-mono text-sm font-semibold">
          {{ amountSign(row as LedgerEntry) }}{{ formatAmount((row as LedgerEntry).amount) }}
        </span>
        <span class="ml-1 text-xs text-text-muted">{{ (row as LedgerEntry).currency }}</span>
      </template>

      <template #cell-flow="{ row }">
        <div class="flex items-center gap-2">
          <BalancePill :balance="(row as LedgerEntry).from_balance" />
          <ArrowRight class="h-3.5 w-3.5 text-text-muted" />
          <BalancePill :balance="(row as LedgerEntry).to_balance" />
        </div>
      </template>

      <template #cell-reference="{ row }">
        <RouterLink
          v-if="refLinkHref(row as LedgerEntry)"
          :to="refLinkHref(row as LedgerEntry)!"
          class="text-sm text-accent hover:underline"
        >
          {{ refLabel(row as LedgerEntry) }}
        </RouterLink>
        <span v-else class="text-sm text-text-muted">{{ refLabel(row as LedgerEntry) || '—' }}</span>
      </template>

      <template #cell-created_at="{ value }">
        {{ formatDate(value) }}
      </template>
    </DataTable>

    <DataTable
      v-else
      :columns="withdrawalColumns"
      :rows="withdrawals"
      :loading="withdrawalsLoading"
      row-key="id"
      :current-page="withdrawalPage"
      :total-pages="withdrawalTotalPages"
      @page-change="p => { withdrawalPage = p; loadWithdrawals() }"
    >
      <template #cell-amount="{ row }">
        <span class="font-bold text-text-main">{{ formatAmount((row as WithdrawalRequest).amount) }}</span>
        <span class="ml-1 text-xs text-text-muted">{{ (row as WithdrawalRequest).currency }}</span>
      </template>
      <template #cell-fee_amount="{ row }">
        <span class="text-text-secondary">{{ formatAmount((row as WithdrawalRequest).fee_amount) }}</span>
      </template>
      <template #cell-destination_address="{ value }">
        <span class="font-mono text-xs text-text-secondary">{{ value }}</span>
      </template>
      <template #cell-status="{ value }">
        <StatusBadge :status="value" />
      </template>
      <template #cell-created_at="{ value }">
        {{ formatDate(value) }}
      </template>
    </DataTable>

    <BaseModal v-model="showRequest" title="Запрос на вывод">
      <div class="space-y-4">
        <BaseInput v-model.number="requestForm.amount" type="number" label="Сумма" required />
        <BaseSelect v-model="requestForm.currency" label="Валюта" :options="currencyOpts" />
        <BaseInput v-model="requestForm.destination_address" label="Адрес получателя" required />
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <BaseButton variant="ghost" @click="showRequest = false">Отмена</BaseButton>
          <BaseButton variant="gold" :loading="submitting" @click="submitRequest">Создать</BaseButton>
        </div>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { computed, h, onMounted, reactive, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { ArrowRight, Download, Plus } from 'lucide-vue-next'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseFilter from '@/components/ui/BaseFilter.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseTabs from '@/components/ui/BaseTabs.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { financesService } from '@/api/services/finances.service'
import { useToast } from '@/composables/useToast'
import { formatAmount, formatDate } from '@/utils/format'
import { currencyOptions, withdrawalStatusOptionsWithAll } from '@/constants'
import type { BalanceInfo, BalanceRefInfo, BalanceType, LedgerEntry, TraderFinanceStats, WithdrawalRequest } from '@/types'

const toast = useToast()
const WITHDRAWAL_PER_PAGE = 25

type BadgeColor = 'success' | 'default' | 'danger' | 'gold' | 'info' | 'warning'

const activeTab = ref('ledger')
const tabs = [
  { key: 'ledger', label: 'Операции' },
  { key: 'withdrawals', label: 'Выводы' },
]

const balances = ref<BalanceInfo[]>([])
const stats = ref<TraderFinanceStats | null>(null)
const ledgerEntries = ref<LedgerEntry[]>([])
const withdrawals = ref<WithdrawalRequest[]>([])
const ledgerLoading = ref(true)
const withdrawalsLoading = ref(false)
const statsLoading = ref(false)
const ledgerPage = ref(1)
const withdrawalPage = ref(1)
const perPage = ref(50)
const ledgerTotalPages = ref(1)
const withdrawalTotalPages = ref(1)

const ledgerFilters = reactive({
  reference_type: '',
  order_search: '',
  amount_from: '',
  amount_to: '',
})
const withdrawalFilters = reactive({ status: '' })
const statsFilters = reactive({
  date_from: defaultDateFrom(),
  date_to: todayISO(),
})

const showRequest = ref(false)
const submitting = ref(false)
const requestForm = reactive({
  amount: 0,
  currency: 'USDT',
  destination_address: '',
})
const currencyOpts = currencyOptions

const ledgerColumns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'reference_type', label: 'Тип' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'flow', label: 'Движение' },
  { key: 'reference', label: 'Источник' },
  { key: 'description', label: 'Описание' },
  { key: 'created_at', label: 'Дата' },
]

const withdrawalColumns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'fee_amount', label: 'Комиссия', align: 'right' },
  { key: 'destination_address', label: 'Адрес' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Дата' },
]

const ledgerTypeOptions = [
  { value: '', label: 'Все' },
  { value: 'deposit', label: 'Пополнение' },
  { value: 'crypto_deposit', label: 'Пополнение (крипто)' },
  { value: 'withdrawal', label: 'Вывод' },
  { value: 'internal_transfer', label: 'Перевод escrow/work' },
  { value: 'trader_reward', label: 'Вознаграждение трейдера' },
  { value: 'order_payin', label: 'Ордер payin' },
  { value: 'order_payout', label: 'Ордер payout' },
  { value: 'dispute_refund', label: 'Возврат по спору' },
]

const statusOptions = withdrawalStatusOptionsWithAll

const ledgerActiveFilters = computed(() => {
  return [ledgerFilters.reference_type, ledgerFilters.order_search, ledgerFilters.amount_from, ledgerFilters.amount_to]
    .filter(Boolean).length
})

const balanceTypeLabels: Record<BalanceType, string> = {
  work: 'Рабочий',
  escrow: 'Эскроу',
  safe_deposit: 'Сейф',
}

const BalancePill = (props: { balance?: BalanceRefInfo | null }) => {
  const b = props.balance
  if (!b) return h('span', { class: 'text-text-muted' }, '—')
  const typeLabel = b.type === 'work' ? 'WORK' : b.type === 'escrow' ? 'ESCROW' : 'SAFE'
  const typeColor =
    b.type === 'work'
      ? 'bg-status-success/15 text-status-success border-status-success/30'
      : b.type === 'escrow'
        ? 'bg-status-warning/15 text-status-warning border-status-warning/30'
        : 'bg-bg-hover text-text-muted border-border'
  return h(
    'span',
    {
      class: `inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-semibold ${typeColor}`,
      title: b.owner_label,
    },
    `${typeLabel} · ${b.currency}`,
  )
}


function todayISO(): string {
  return new Date().toISOString().slice(0, 10)
}

function defaultDateFrom(): string {
  const d = new Date()
  d.setDate(d.getDate() - 30)
  return d.toISOString().slice(0, 10)
}

function csvEscape(value: string | number): string {
  const raw = String(value ?? '')
  return `"${raw.replace(/"/g, '""')}"`
}

function exportStats() {
  if (!stats.value?.orders.length) {
    toast.error('Нет данных для выгрузки')
    return
  }

  const rows = [
    ['Номер заявки', 'Дата заявки', 'Сумма заявки USDT', 'Прибыль USDT'],
    ...stats.value.orders.map(order => [
      order.order_id,
      formatDate(order.order_date),
      order.amount_usdt,
      order.profit_usdt,
    ]),
  ]
  const csv = rows.map(row => row.map(csvEscape).join(';')).join('\n')
  const blob = new Blob([`\uFEFF${csv}`], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `trader-finance-${statsFilters.date_from || 'start'}-${statsFilters.date_to || 'end'}.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

function balanceTypeLabel(type: BalanceType): string {
  return balanceTypeLabels[type] ?? type
}

function formatType(type: string): string {
  const opt = ledgerTypeOptions.find(t => t.value === type)
  return opt?.label ?? type
}

function typeColor(type: string): BadgeColor {
  const colors: Record<string, BadgeColor> = {
    order_payin: 'success',
    order_payout: 'warning',
    deposit: 'info',
    crypto_deposit: 'info',
    withdrawal: 'danger',
    internal_transfer: 'gold',
    dispute_refund: 'danger',
    trader_reward: 'success',
  }
  return colors[type] ?? 'default'
}

function isOwnBalance(balance?: BalanceRefInfo | null): boolean {
  return balance?.owner_kind === 'trader' || balance?.owner_kind === 'teamlead'
}

function isIncoming(row: LedgerEntry): boolean {
  const to = row.to_balance
  const from = row.from_balance
  if (isOwnBalance(to) && isOwnBalance(from)) return to?.type === 'work'
  if (isOwnBalance(to)) return true
  if (isOwnBalance(from)) return false
  return row.reference_type !== 'withdrawal'
}

function amountSign(row: LedgerEntry): string {
  return isIncoming(row) ? '+' : '-'
}

function amountClass(row: LedgerEntry): string {
  return isIncoming(row) ? 'text-status-success' : 'text-status-danger'
}

function refLabel(row: LedgerEntry): string {
  const type = row.reference_type
  if (!row.reference_id) return ''
  if (type === 'withdrawal') return `Вывод #${row.reference_id}`
  if (type === 'deposit') return `Пополнение #${row.reference_id}`
  if (type === 'crypto_deposit') return 'Крипто-пополнение'
  if (['order_payin', 'order_payout', 'trader_reward', 'dispute_refund'].includes(type)) {
    return `Ордер #${row.reference_id}`
  }
  return row.reference_id
}

function refLinkHref(row: LedgerEntry): { path: string; query?: Record<string, string> } | null {
  if (!row.reference_id) return null
  if (['order_payin', 'order_payout', 'trader_reward', 'dispute_refund'].includes(row.reference_type)) {
    return { path: '/trader/orders', query: { id_search: row.reference_id } }
  }
  return null
}

function resetLedgerFilters() {
  ledgerFilters.reference_type = ''
  ledgerFilters.order_search = ''
  ledgerFilters.amount_from = ''
  ledgerFilters.amount_to = ''
  ledgerPage.value = 1
  loadLedger()
}

function resetWithdrawalFilters() {
  withdrawalPage.value = 1
  withdrawalFilters.status = ''
  loadWithdrawals()
}

async function loadBalances() {
  try {
    const { data } = await financesService.listMyBalances()
    balances.value = data
  } catch { /* soft-fail */ }
}


async function loadStats() {
  statsLoading.value = true
  try {
    const params: Record<string, string> = {}
    if (statsFilters.date_from) params.date_from = statsFilters.date_from
    if (statsFilters.date_to) params.date_to = statsFilters.date_to
    const { data } = await financesService.getMyStats(params)
    stats.value = data
  } catch {
    toast.error('Ошибка загрузки статистики')
  } finally {
    statsLoading.value = false
  }
}

async function loadLedger() {
  ledgerLoading.value = true
  try {
    const params: Record<string, any> = { skip: (ledgerPage.value - 1) * perPage.value, limit: perPage.value }
    if (ledgerFilters.reference_type) params.reference_type = ledgerFilters.reference_type
    if (ledgerFilters.order_search) params.order_search = ledgerFilters.order_search
    if (ledgerFilters.amount_from) params.amount_from = Number(ledgerFilters.amount_from)
    if (ledgerFilters.amount_to) params.amount_to = Number(ledgerFilters.amount_to)
    const { data } = await financesService.listMyLedger(params)
    ledgerEntries.value = data
    ledgerTotalPages.value = data.length < perPage.value ? ledgerPage.value : ledgerPage.value + 1
  } catch {
    toast.error('Ошибка загрузки операций')
  } finally {
    ledgerLoading.value = false
  }
}

async function loadWithdrawals() {
  withdrawalsLoading.value = true
  try {
    const params: Record<string, any> = { skip: (withdrawalPage.value - 1) * WITHDRAWAL_PER_PAGE, limit: WITHDRAWAL_PER_PAGE }
    if (withdrawalFilters.status) params.status = withdrawalFilters.status
    const { data } = await financesService.listMyWithdrawals(params)
    withdrawals.value = data
    withdrawalTotalPages.value = data.length < WITHDRAWAL_PER_PAGE ? withdrawalPage.value : withdrawalPage.value + 1
  } catch {
    toast.error('Ошибка загрузки выводов')
  } finally {
    withdrawalsLoading.value = false
  }
}

function openRequest() {
  requestForm.amount = 0
  requestForm.currency = 'USDT'
  requestForm.destination_address = ''
  showRequest.value = true
}

async function submitRequest() {
  if (!requestForm.amount || requestForm.amount <= 0) {
    toast.error('Укажите сумму')
    return
  }
  if (!requestForm.destination_address.trim()) {
    toast.error('Укажите адрес получателя')
    return
  }
  submitting.value = true
  try {
    await financesService.createMyWithdrawal({
      amount: requestForm.amount,
      currency: requestForm.currency,
      destination_address: requestForm.destination_address.trim(),
    })
    toast.success('Запрос создан')
    showRequest.value = false
    activeTab.value = 'withdrawals'
    await Promise.all([loadWithdrawals(), loadBalances(), loadLedger(), loadStats()])
  } catch (e: any) {
    toast.error(e?.response?.data?.error_message || e?.response?.data?.detail || 'Ошибка создания')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadStats(), loadLedger(), loadWithdrawals(), loadBalances()])
})
</script>
