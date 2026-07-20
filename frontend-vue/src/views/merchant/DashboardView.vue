<template>
  <div>
    <AccountHero
      role="merchant"
      eyebrow="Кабинет мерчанта"
      title="Платежный контур под контролем"
      subtitle="Сводка по терминалам, балансу, конверсии и последним операциям в одном защищенном рабочем пространстве."
      :metrics="heroMetrics"
    >
      <template #actions>
        <div class="grid w-full grid-cols-2 gap-3 sm:flex sm:w-auto sm:items-center sm:gap-2">
          <BaseDatePicker
            v-model="dateFrom"
            with-time
            placeholder="От"
            button-class="w-full sm:w-[190px]"
            @change="loadStats"
          />
          <BaseDatePicker
            v-model="dateTo"
            with-time
            placeholder="До"
            button-class="w-full sm:w-[190px]"
            @change="loadStats"
          />
        </div>
      </template>
    </AccountHero>

    <div v-if="loadingProfile" class="py-16"><LoadingSpinner /></div>

    <template v-else-if="profile">
      <section class="mb-6 grid gap-4 xl:grid-cols-[minmax(0,1.08fr)_minmax(360px,0.92fr)]">
        <div class="relative overflow-hidden rounded-[1.35rem] border border-accent/15 bg-bg-surface/70 p-5 shadow-[0_24px_76px_rgba(0,0,0,0.3),inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl">
          <div class="pointer-events-none absolute inset-0 sp-panel-grid opacity-45" />
          <div class="relative mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p class="sp-kicker">Финансовый контур</p>
              <h2 class="mt-2 text-3xl font-black leading-none text-text-main">Финансовый пульт мерчанта</h2>
            </div>
            <span class="inline-flex w-fit items-center gap-2 rounded-xl border border-status-success/20 bg-status-success/10 px-3 py-2 text-xs font-black text-status-success">
              <span class="sp-status-dot" />
              {{ statusLabel }}
            </span>
          </div>

          <div class="relative grid gap-3 sm:grid-cols-2">
            <article
              v-for="metric in cockpitMetrics"
              :key="metric.label"
              class="min-h-[142px] rounded-[1rem] border border-accent/15 bg-bg-main/60 p-4 shadow-[inset_0_1px_0_rgba(245,245,245,0.035)]"
            >
              <div class="mb-5 flex items-center justify-between gap-3">
                <p class="text-[11px] font-black uppercase tracking-[0.14em] text-text-muted">{{ metric.label }}</p>
                <span class="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-accent/15 bg-accent-dark/10 text-accent">
                  <component :is="metric.icon" class="h-4 w-4" />
                </span>
              </div>
              <strong class="block break-words text-3xl font-black leading-none text-text-main">{{ metric.value }}</strong>
              <p class="mt-3 text-xs font-semibold leading-5 text-text-muted">{{ metric.caption }}</p>
            </article>
          </div>
        </div>

        <div class="grid gap-4">
          <div class="rounded-[1.35rem] border border-accent/15 bg-bg-surface/70 p-5 shadow-[0_24px_76px_rgba(0,0,0,0.26),inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl">
            <div class="mb-5 flex items-center justify-between gap-3">
              <div>
                <p class="sp-kicker">Процессинг</p>
                <h2 class="mt-2 text-2xl font-black leading-none text-text-main">Здоровье потока</h2>
              </div>
              <span class="rounded-xl border border-accent/20 bg-accent-dark/10 px-3 py-2 text-sm font-black text-accent">
                {{ approvalPct }}%
              </span>
            </div>
            <div class="space-y-4">
              <div>
                <div class="mb-2 flex items-center justify-between text-xs font-black text-text-muted">
                  <span>Одобрение</span>
                  <span>{{ stats.orders_success }} / {{ stats.orders_total }}</span>
                </div>
                <div class="h-2 overflow-hidden rounded-full bg-white/10">
                  <div class="h-full rounded-full bg-gradient-to-r from-accent-dark via-accent to-status-success" :style="{ width: `${approvalPct}%` }" />
                </div>
              </div>
              <div>
                <div class="mb-2 flex items-center justify-between text-xs font-black text-text-muted">
                  <span>Отказы</span>
                  <span>{{ failedPct }}%</span>
                </div>
                <div class="h-2 overflow-hidden rounded-full bg-white/10">
                  <div class="h-full rounded-full bg-status-danger/70" :style="{ width: `${failedPct}%` }" />
                </div>
              </div>
              <div class="grid grid-cols-3 gap-2 pt-1">
                <div v-for="item in flowRows" :key="item.label" class="rounded-xl border border-accent/10 bg-bg-main/45 p-3">
                  <span class="block text-[10px] font-black uppercase tracking-[0.12em] text-text-muted">{{ item.label }}</span>
                  <strong class="mt-2 block text-lg font-black text-text-main">{{ item.value }}</strong>
                </div>
              </div>
            </div>
          </div>

          <div class="rounded-[1.35rem] border border-accent/15 bg-bg-surface/70 p-5 shadow-[0_24px_76px_rgba(0,0,0,0.26),inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl">
            <p class="sp-kicker mb-4">Расчетный слой</p>
            <div class="grid gap-2">
              <div
                v-for="row in settlementRows"
                :key="row.label"
                class="grid min-h-[48px] grid-cols-[1fr_auto] items-center gap-3 rounded-xl border border-accent/10 bg-bg-main/45 px-4"
              >
                <span class="text-sm font-semibold text-text-muted">{{ row.label }}</span>
                <strong class="text-right text-sm font-black text-text-main">{{ row.value }}</strong>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section class="mb-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
        <BaseCard title="Платежные методы">
          <div v-if="!methodRows.length" class="py-4 text-center text-sm text-text-muted">
            Нет доступных методов
          </div>
          <div v-else class="grid gap-3">
            <div
              v-for="pm in methodRows"
              :key="pm.method"
              class="flex min-h-[58px] items-center justify-between rounded-xl border border-accent/10 bg-bg-main/45 px-4 py-3 shadow-[inset_0_1px_0_rgba(245,245,245,0.035)]"
            >
              <MethodBadge :method="pm.method" />
              <span class="rounded-lg border border-accent/20 bg-accent-dark/10 px-3 py-1 text-sm font-black text-accent">{{ pm.fee_percentage }}%</span>
            </div>
          </div>
        </BaseCard>

        <div class="lg:col-span-2">
          <BaseCard title="Последние ордера">
            <DataTable
              :columns="orderCols"
              :rows="recentOrders"
              row-key="id"
              :loading="loadingOrders"
            >
              <template #cell-uuid="{ value }">
                <UuidDisplay :value="String(value)" show-icon />
              </template>
              <template #cell-merchant_name="{ row }">
                <span class="text-xs text-text-main">
                  {{ row.merchant_name || `Терминал #${row.merchant_id}` }}
                </span>
              </template>
              <template #cell-amount="{ row }">
                {{ formatAmount(row.amount) }} {{ row.currency }}
              </template>
              <template #cell-status="{ value }">
                <StatusBadge :status="value" />
              </template>
              <template #cell-created_at="{ value }">
                {{ formatDate(value) }}
              </template>
            </DataTable>
            <div class="mt-3 text-right">
              <router-link to="/merchant/orders" class="text-sm font-bold text-accent hover:underline">
                Все ордера →
              </router-link>
            </div>
          </BaseCard>
        </div>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import AccountHero from '@/components/layout/AccountHero.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseDatePicker from '@/components/ui/BaseDatePicker.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import { merchantsService } from '@/api/services/merchants.service'
import { financesService } from '@/api/services/finances.service'
import { useToast } from '@/composables/useToast'
import { formatAmount, formatInt, formatDate } from '@/utils/format'
import { toUnixTs } from '@/utils/datetime'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import type { BalanceInfo, MerchantFullProfile, MerchantStats, Order, PaymentMethod } from '@/types'
import { ALL_PAYMENT_METHODS, merchantStatusLabels } from '@/constants'
import { Wallet, TrendingUp, Target, AlertTriangle } from 'lucide-vue-next'

const toast = useToast()

const dateFrom = ref('')
const dateTo = ref('')

const loadingProfile = ref(true)
const loadingOrders = ref(true)
const loadingStats = ref(true)

const profile = ref<MerchantFullProfile | null>(null)
const stats = ref<MerchantStats>({
  turnover_usdt: 0, fee_usdt: 0,
  orders_total: 0, orders_success: 0, orders_active: 0, orders_failed: 0,
  conversion_pct: 0, pending_withdrawals: 0, active_disputes: 0,
})
const recentOrders = ref<Order[]>([])

const balances = ref<BalanceInfo[]>([])
const terminalCount = ref(0)
const totalWorkUsdt = computed(() => {
  const row = balances.value.find(b => b.type === 'work' && b.currency === 'USDT')
  return row ? row.amount : 0
})
const totalEscrowUsdt = computed(() => {
  const row = balances.value.find(b => b.type === 'escrow' && b.currency === 'USDT')
  return row ? row.amount : 0
})

function pluralize(n: number, forms: [string, string, string]): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return forms[0]
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return forms[1]
  return forms[2]
}

const statusLabel = computed(() => {
  return profile.value
    ? merchantStatusLabels[profile.value.status] ?? profile.value.status
    : '—'
})

const heroMetrics = computed(() => [
  {
    label: 'Рабочий баланс',
    value: `${formatAmount(totalWorkUsdt.value)} USDT`,
    caption: terminalCount.value > 0 ? `${terminalCount.value} ${pluralize(terminalCount.value, ['терминал', 'терминала', 'терминалов'])}` : 'Терминалы не найдены',
  },
  {
    label: 'Конверсия',
    value: `${Math.round(stats.value.conversion_pct)}%`,
    caption: `${stats.value.orders_success} успешных ордеров`,
  },
  {
    label: 'Активные ордера',
    value: stats.value.orders_active,
    caption: stats.value.active_disputes ? `${stats.value.active_disputes} спорных операций` : 'Споров нет',
  },
  {
    label: 'Статус контура',
    value: statusLabel.value,
    caption: profile.value?.currency ? `Расчеты в ${profile.value.currency}` : 'Профиль загружается',
  },
])

function pct(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.max(0, Math.min(100, Math.round(value)))
}

const approvalPct = computed(() => pct(stats.value.conversion_pct))
const failedPct = computed(() =>
  stats.value.orders_total > 0
    ? pct((stats.value.orders_failed / stats.value.orders_total) * 100)
    : 0,
)

const cockpitMetrics = computed(() => [
  {
    label: 'Рабочий баланс',
    value: `${formatAmount(totalWorkUsdt.value)} USDT`,
    caption: terminalCount.value > 0
      ? `${terminalCount.value} ${pluralize(terminalCount.value, ['терминал', 'терминала', 'терминалов'])} в контуре`
      : 'Терминалы не найдены',
    icon: Wallet,
  },
  {
    label: 'Оборот',
    value: `${formatInt(stats.value.turnover_usdt)} USDT`,
    caption: `${stats.value.orders_total} операций за период`,
    icon: TrendingUp,
  },
  {
    label: 'Конверсия',
    value: `${approvalPct.value}%`,
    caption: `${stats.value.orders_success} успешных ордеров`,
    icon: Target,
  },
  {
    label: 'Риски',
    value: stats.value.active_disputes ? `${stats.value.active_disputes} споров` : 'Чисто',
    caption: stats.value.orders_failed ? `${stats.value.orders_failed} неудачных операций` : 'Критичных сигналов нет',
    icon: AlertTriangle,
  },
])

const flowRows = computed(() => [
  { label: 'Активные', value: stats.value.orders_active },
  { label: 'Выводы', value: stats.value.pending_withdrawals },
  { label: 'Методы', value: methodRows.value.length },
])

const settlementRows = computed(() => [
  { label: 'Рабочий баланс', value: `${formatAmount(totalWorkUsdt.value)} USDT` },
  { label: 'Эскроу резерв', value: `${formatAmount(totalEscrowUsdt.value)} USDT` },
  { label: 'Комиссии', value: `${formatInt(stats.value.fee_usdt)} USDT` },
  { label: 'Терминалы', value: String(terminalCount.value) },
])

const methodRows = computed(() => {
  const p = profile.value
  if (!p) return [] as Array<{ method: PaymentMethod; fee_percentage: number }>
  const byMethod: Record<string, { fee_percentage: number }> = {}
  for (const pm of p.payment_methods ?? []) {
    byMethod[pm.method] = { fee_percentage: pm.fee_percentage }
  }
  for (const key of Object.keys(p.fees ?? {})) {
    if (!(key in byMethod)) {
      byMethod[key] = { fee_percentage: Number(p.fees?.[key] ?? 0) }
    }
  }
  return ALL_PAYMENT_METHODS
    .filter(m => m in byMethod)
    .map(m => ({ method: m, ...byMethod[m] }))
})

const orderCols: Column[] = [
  { key: 'uuid', label: 'UUID' },
  { key: 'merchant_name', label: 'Терминал' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Дата' },
]

async function loadProfile() {
  try {
    const { data } = await merchantsService.getMyProfile()
    profile.value = data
  } catch {
    toast.error('Ошибка загрузки профиля')
  } finally {
    loadingProfile.value = false
  }
}

async function loadStats() {
  loadingStats.value = true
  try {
    const params: Record<string, number> = {}
    if (dateFrom.value) params.date_from = toUnixTs(dateFrom.value)
    if (dateTo.value) params.date_to = toUnixTs(dateTo.value, { endOfDay: true })
    const { data } = await merchantsService.getMyStats(params)
    stats.value = data
  } catch {
    toast.error('Ошибка загрузки статистики')
  } finally {
    loadingStats.value = false
  }
}

async function loadOrders() {
  loadingOrders.value = true
  try {
    const { data } = await merchantsService.listMyOrders({ limit: 10 })
    recentOrders.value = data.items
  } catch {
    toast.error('Ошибка загрузки ордеров')
  } finally {
    loadingOrders.value = false
  }
}

async function loadBalances() {
  try {
    const { data } = await financesService.listMyBalances()
    balances.value = data
  } catch {
    /* non-critical */
  }
}

async function loadTerminals() {
  try {
    const { data } = await merchantsService.listMyMerchants()
    terminalCount.value = data.length
  } catch {
    /* non-critical */
  }
}

onMounted(() => {
  loadProfile()
  loadStats()
  loadOrders()
  loadBalances()
  loadTerminals()
})
</script>
