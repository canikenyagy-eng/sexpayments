<template>
  <div>
    <PageHeader title="Финансы">
      <template #actions>
        <BaseButton variant="gold" @click="openRequest">+ Запрос на вывод</BaseButton>
      </template>
    </PageHeader>

    <section class="mb-5 grid gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(360px,0.72fr)]">
      <div class="relative overflow-hidden rounded-[1.25rem] border border-accent/15 bg-bg-surface/70 p-5 shadow-[0_22px_70px_rgba(0,0,0,0.28),inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl">
        <div class="pointer-events-none absolute inset-0 sp-panel-grid opacity-35" />
        <div class="relative mb-5 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p class="sp-kicker">Финансовый контур</p>
            <h2 class="mt-2 text-2xl font-black leading-none text-text-main">Портфель тимлида</h2>
            <p class="mt-3 max-w-2xl text-sm font-semibold leading-6 text-text-muted">
              Балансы, сделки трейдеров и запросы на вывод собраны в одном расчетном слое.
            </p>
          </div>
          <span class="inline-flex w-fit items-center gap-2 rounded-xl border border-status-success/20 bg-status-success/10 px-3 py-2 text-xs font-black text-status-success">
            <span class="sp-status-dot" />
            Контур активен
          </span>
        </div>
        <div class="relative grid gap-3 sm:grid-cols-3">
          <article
            v-for="b in balances"
            :key="b.id"
            class="min-h-[116px] rounded-xl border border-accent/10 bg-bg-main/55 p-4"
          >
            <span class="block text-[10px] font-black uppercase tracking-[0.12em] text-text-muted">
              {{ balanceTypeLabel(b.type) }} · {{ b.currency }}
            </span>
            <strong class="mt-4 block break-words text-2xl font-black leading-none text-text-main">
              {{ formatAmount(b.amount) }}
            </strong>
          </article>
          <article v-if="!balances.length" class="min-h-[116px] rounded-xl border border-dashed border-accent/15 bg-bg-main/45 p-4">
            <span class="block text-[10px] font-black uppercase tracking-[0.12em] text-text-muted">Балансы</span>
            <strong class="mt-4 block text-lg font-black text-text-main">Нет данных</strong>
          </article>
        </div>
      </div>

      <div class="rounded-[1.25rem] border border-accent/15 bg-bg-surface/70 p-5 shadow-[0_22px_70px_rgba(0,0,0,0.24),inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl">
        <p class="sp-kicker">Рабочий срез</p>
        <h2 class="mt-2 text-xl font-black leading-none text-text-main">Финансовая лента</h2>
        <div class="mt-5">
          <BaseTabs v-model="activeTab" :tabs="tabs" />
        </div>
        <div v-if="activeTab === 'withdrawals'" class="mt-4 rounded-xl border border-accent/10 bg-bg-main/45 p-3">
          <BaseSelect v-model="withdrawalFilters.status" label="Статус вывода" :options="statusOptions" />
          <div class="mt-3 flex justify-end gap-2">
            <BaseButton v-if="withdrawalFilters.status" variant="ghost" size="sm" @click="resetWithdrawalFilters">
              Сбросить
            </BaseButton>
            <BaseButton variant="dark" size="sm" @click="withdrawalPage = 1; loadWithdrawals()">
              Применить
            </BaseButton>
          </div>
        </div>
        <div class="mt-4 grid grid-cols-3 gap-2">
          <div
            v-for="item in summaryRows"
            :key="item.label"
            class="rounded-xl border border-accent/10 bg-bg-main/45 p-3"
          >
            <span class="block text-[10px] font-black uppercase tracking-[0.12em] text-text-muted">{{ item.label }}</span>
            <strong class="mt-2 block text-lg font-black text-text-main">{{ item.value }}</strong>
          </div>
        </div>
      </div>
    </section>

    <DataTable
      v-if="activeTab === 'orders'"
      :columns="orderColumns"
      :rows="traderOrders"
      :loading="ordersLoading"
      row-key="order_id"
      :current-page="ordersPage"
      :total-pages="ordersTotalPages"
      :per-page="perPage"
      empty-text="Сделок в финансовой ленте нет"
      @page-change="p => { ordersPage = p; loadTraderOrders() }"
      @per-page-change="n => { perPage = n; ordersPage = 1; loadTraderOrders() }"
    >
      <template #cell-order_id="{ value }">
        <span class="font-mono text-sm text-text-main">#{{ value }}</span>
      </template>
      <template #cell-closed_at="{ value }">
        {{ formatDate(value) }}
      </template>
      <template #cell-amount_usdt="{ row }">
        <span class="font-bold text-text-main">{{ formatAmount((row as TeamleadTraderOrderFinance).amount_usdt) }}</span>
        <span class="ml-1 text-xs text-text-muted">USDT</span>
      </template>
      <template #cell-teamlead_profit_usdt="{ row }">
        <span class="font-bold text-status-success">{{ formatAmount((row as TeamleadTraderOrderFinance).teamlead_profit_usdt) }}</span>
        <span class="ml-1 text-xs text-text-muted">USDT</span>
      </template>
      <template #cell-trader_login="{ row }">
        <div class="text-sm text-text-main">{{ (row as TeamleadTraderOrderFinance).trader_login }}</div>
        <div class="text-xs text-text-muted">ID {{ (row as TeamleadTraderOrderFinance).trader_id }}</div>
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
      empty-text="Запросов на вывод по выбранному статусу нет"
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
import { computed, ref, reactive, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTabs from '@/components/ui/BaseTabs.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { financesService } from '@/api/services/finances.service'
import { teamleadsService } from '@/api/services/teamleads.service'
import { useToast } from '@/composables/useToast'
import { formatAmount, formatDate } from '@/utils/format'
import { currencyOptions, withdrawalStatusOptionsWithAll } from '@/constants'
import type { BalanceInfo, BalanceType, TeamleadTraderOrderFinance, WithdrawalRequest } from '@/types'

const toast = useToast()
const WITHDRAWAL_PER_PAGE = 25

const activeTab = ref('orders')
const tabs = [
  { key: 'orders', label: 'Сделки' },
  { key: 'withdrawals', label: 'Выводы' },
]

const balances = ref<BalanceInfo[]>([])
const traderOrders = ref<TeamleadTraderOrderFinance[]>([])
const withdrawals = ref<WithdrawalRequest[]>([])
const ordersLoading = ref(true)
const withdrawalsLoading = ref(false)
const ordersPage = ref(1)
const withdrawalPage = ref(1)
const perPage = ref(50)
const ordersTotalPages = ref(1)
const withdrawalTotalPages = ref(1)
const withdrawalFilters = reactive({ status: '' })
const totalBalanceUsdt = computed(() =>
  balances.value
    .filter(balance => balance.currency === 'USDT')
    .reduce((sum, balance) => sum + Number(balance.amount ?? 0), 0),
)
const visibleFinanceRows = computed(() =>
  activeTab.value === 'orders' ? traderOrders.value.length : withdrawals.value.length,
)
const summaryRows = computed(() => [
  { label: 'USDT', value: formatAmount(totalBalanceUsdt.value) },
  { label: 'Строки', value: visibleFinanceRows.value },
  { label: 'Режим', value: activeTab.value === 'orders' ? 'Сделки' : 'Выводы' },
])

const showRequest = ref(false)
const submitting = ref(false)
const requestForm = reactive({
  amount: 0,
  currency: 'USDT',
  destination_address: '',
})
const currencyOpts = currencyOptions

const orderColumns: Column[] = [
  { key: 'order_id', label: 'ID сделки' },
  { key: 'closed_at', label: 'Дата сделки' },
  { key: 'amount_usdt', label: 'Сумма сделки', align: 'right' },
  { key: 'teamlead_profit_usdt', label: 'Прибыль тимлида', align: 'right' },
  { key: 'trader_login', label: 'Трейдер' },
]

const withdrawalColumns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'fee_amount', label: 'Комиссия', align: 'right' },
  { key: 'destination_address', label: 'Адрес' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Дата' },
]

const statusOptions = withdrawalStatusOptionsWithAll

const balanceTypeLabels: Record<BalanceType, string> = {
  work: 'Рабочий',
  escrow: 'Эскроу',
  safe_deposit: 'Сейф',
}

function balanceTypeLabel(type: BalanceType): string {
  return balanceTypeLabels[type] ?? type
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

async function loadTraderOrders() {
  ordersLoading.value = true
  try {
    const { data } = await teamleadsService.getMyTraderOrders({
      skip: (ordersPage.value - 1) * perPage.value,
      limit: perPage.value,
    })
    traderOrders.value = data
    ordersTotalPages.value = data.length < perPage.value ? ordersPage.value : ordersPage.value + 1
  } catch {
    toast.error('Ошибка загрузки сделок')
  } finally {
    ordersLoading.value = false
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
  } catch { toast.error('Ошибка загрузки выводов') }
  finally { withdrawalsLoading.value = false }
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
    await Promise.all([loadWithdrawals(), loadBalances()])
  } catch (e: any) {
    toast.error(e?.response?.data?.error_message || e?.response?.data?.detail || 'Ошибка создания')
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadTraderOrders(), loadWithdrawals(), loadBalances()])
})
</script>
