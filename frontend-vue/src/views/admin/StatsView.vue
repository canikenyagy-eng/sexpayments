<template>
  <div>
    <PageHeader title="Статистика" />

    <!-- Tabs -->
    <div class="mb-4 flex gap-1 overflow-x-auto rounded-xl bg-bg-surface p-1 no-scrollbar">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="shrink-0 rounded-lg px-4 py-2 text-sm font-bold transition"
        :class="activeTab === t.key ? 'bg-accent/15 text-accent' : 'text-text-muted hover:text-text-main'"
        @click="activeTab = t.key"
      >
        {{ t.label }}
      </button>
    </div>

    <!-- Tab Content: Запросы -->
    <div v-if="activeTab === 'requests'">
      <!-- Stats Cards -->
      <div class="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          v-for="card in statCards"
          :key="card.label"
          :label="card.label"
          :value="card.value"
          :icon="card.icon"
          :loading="loadingStats"
        />
      </div>

      <!-- Volume Distribution Table -->
      <div class="mb-6">
        <h3 class="mb-3 px-1 text-sm font-bold text-text-main">Объем сделок (RUB) за 24ч</h3>
        <DataTable
          :columns="volumeCols"
          :rows="volumeRows"
          :loading="loadingVolume"
          row-key="method"
          :show-pagination="false"
        >
          <template #cell-method="{ value }">
            <MethodBadge :method="value as string" size="md" />
          </template>
          <template #cell-lt_1000="{ value }">
            <span class="font-bold text-text-main">{{ formatInt(value as number) }}</span>
          </template>
          <template #cell-from_1000="{ value }">
            <span class="font-bold text-text-main">{{ formatInt(value as number) }}</span>
          </template>
          <template #cell-from_5000="{ value }">
            <span class="font-bold text-text-main">{{ formatInt(value as number) }}</span>
          </template>
          <template #cell-from_8000="{ value }">
            <span class="font-bold text-text-main">{{ formatInt(value as number) }}</span>
          </template>
          <template #cell-from_10000="{ value }">
            <span class="font-bold text-text-main">{{ formatInt(value as number) }}</span>
          </template>
          <template #cell-from_20000="{ value }">
            <span class="font-bold text-text-main">{{ formatInt(value as number) }}</span>
          </template>
          <template #cell-total="{ value }">
            <span class="font-bold text-accent">{{ formatInt(value as number) }}</span>
          </template>
        </DataTable>
      </div>

      <!-- Datatable -->
      <DataTable
        :columns="columns"
        :rows="logs"
        :loading="loadingLogs"
        row-key="id"
        :current-page="page"
        :total-pages="totalPages"
        :per-page="perPage"
        @page-change="(p: number) => { page = p; loadLogs() }"
        @per-page-change="(n: number) => { perPage = n; page = 1; loadLogs() }"
      >
        <template #cell-merchant_login="{ row }">
          <div class="flex items-center gap-1.5 whitespace-nowrap">
            <span class="text-xs text-text-muted">ID: {{ row.merchant_id || '—' }}</span>
            <span class="font-bold">{{ row.merchant_login || '—' }}</span>
          </div>
        </template>
        <template #cell-amount_rub="{ value }">
          <span class="font-bold text-text-main">
            {{ value != null ? formatAmount(value as number) + ' RUB' : '—' }}
          </span>
        </template>
        <template #cell-method="{ value }">
          <MethodBadge :method="(value as string | null) ?? null" />
        </template>
        <template #cell-success="{ value }">
          <BaseBadge :color="value ? 'success' : 'danger'">
            {{ value ? 'Успех' : 'Неуспех' }}
          </BaseBadge>
        </template>
        <template #cell-response_time_ms="{ value }">
          <span class="text-xs text-text-muted">
            {{ value != null ? value + ' мс' : '—' }}
          </span>
        </template>
        <template #cell-created_at="{ value }">
          {{ formatDate(value) }}
        </template>
      </DataTable>
    </div>

    <!-- Tab Content: Активность -->
    <div v-if="activeTab === 'activity'">
      <!-- Controls -->
      <div class="mb-3 flex flex-wrap items-center gap-2">
        <BaseDatePicker
          v-model="activityDateFrom"
          with-time
          placeholder="От"
          button-class="w-full sm:w-[180px]"
          @change="loadActivity"
        />
        <BaseDatePicker
          v-model="activityDateTo"
          with-time
          placeholder="До"
          button-class="w-full sm:w-[180px]"
          @change="loadActivity"
        />
        <BaseSelect
          :model-value="activityGranularity"
          :options="activityGranularityOptions"
          class="w-[130px]"
          @update:modelValue="(v: string) => { activityGranularity = v as ActivityGranularity; loadActivity() }"
        />
        <BaseSelect
          :model-value="activityCurrency"
          :options="currencyOptions"
          class="w-[110px]"
          @update:modelValue="(v: string) => { activityCurrency = v; loadActivity() }"
        />
      </div>

      <!-- Legend: trader-count color scale (shared source of truth) -->
      <div class="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-text-muted">
        <span v-for="l in traderLegend" :key="l.label" class="flex items-center gap-1.5">
          <span class="h-2.5 w-2.5 rounded-sm" :style="{ background: l.color }" />{{ l.label }}
        </span>
      </div>

      <div class="rounded-xl bg-bg-surface p-3">
        <ActivityRangeChart
          :buckets="activity?.buckets ?? []"
          :x-labels="activityXLabels"
          :format-value="formatInt"
          :loading="loadingActivity"
          empty-text="Нет данных за выбранный период"
        />
      </div>
    </div>

    <!-- Tab Content: Мерчанты -->
    <div v-if="activeTab === 'merchants'">
      <!-- Period controls (shared by table + chart) -->
      <div class="mb-3 flex flex-wrap items-center gap-2">
        <BaseDatePicker
          v-model="merchantDateFrom"
          with-time
          placeholder="От"
          button-class="w-full sm:w-[180px]"
          @change="onMerchantRangeChange"
        />
        <BaseDatePicker
          v-model="merchantDateTo"
          with-time
          placeholder="До"
          button-class="w-full sm:w-[180px]"
          @change="onMerchantRangeChange"
        />
      </div>

      <!-- Per-merchant funnel table (click a row to chart it) -->
      <DataTable
        :columns="merchantColumns"
        :rows="merchantTableRows"
        :loading="loadingMerchants"
        row-key="merchant_id"
        clickable
        :show-pagination="false"
        empty-text="Нет данных за выбранный период"
        @row-click="(row: Record<string, any>) => selectMerchant(row.merchant_id as number)"
      >
        <template #cell-merchant="{ row }">
          <div class="flex items-center gap-1.5 whitespace-nowrap">
            <span class="text-xs text-text-muted">ID: {{ row.merchant_id }}</span>
            <span class="font-bold">{{ row.merchant_login || '—' }}</span>
          </div>
        </template>
        <template #cell-requests="{ value }">
          <span class="font-bold text-text-main">{{ formatInt(value as number) }}</span>
        </template>
        <template #cell-orders_created="{ value }">{{ formatInt(value as number) }}</template>
        <template #cell-orders_success="{ value }">{{ formatInt(value as number) }}</template>
        <template #cell-conversion_pct="{ value }">
          <span class="font-bold text-accent">{{ (value as number).toFixed(2) }}%</span>
        </template>
        <template #cell-payout_pct="{ value }">
          <span class="font-bold text-text-main">{{ (value as number).toFixed(2) }}%</span>
        </template>
      </DataTable>

      <!-- Per-merchant timeframe chart -->
      <div class="mt-6">
        <div class="mb-3 flex flex-wrap items-center gap-2">
          <BaseSelect
            :model-value="selectedMerchantId != null ? String(selectedMerchantId) : ''"
            :options="merchantOptions"
            class="w-full sm:w-[220px]"
            @update:modelValue="(v: string) => selectMerchant(v ? Number(v) : null)"
          />
          <BaseSelect
            :model-value="merchantGranularity"
            :options="granularityOptions"
            class="w-[130px]"
            @update:modelValue="(v: string) => { merchantGranularity = v as Granularity; loadMerchantTimeseries() }"
          />
        </div>
        <div class="rounded-xl bg-bg-surface p-3">
          <MerchantFunnelChart
            v-if="selectedMerchantId != null"
            :points="merchantTs?.points ?? []"
            :x-labels="merchantTsXLabels"
            :loading="loadingMerchantTs"
            empty-text="Нет данных за выбранный период"
          />
          <div v-else class="flex h-[240px] items-center justify-center text-sm text-text-muted">
            Выберите мерчанта (в списке или выше), чтобы увидеть график
          </div>
        </div>
      </div>
    </div>

    <!-- Tab Content: Чекер -->
    <div v-if="activeTab === 'checker'">
      <!-- Period + granularity controls (shared by chart + tables) -->
      <div class="mb-3 flex flex-wrap items-center gap-2">
        <BaseDatePicker
          v-model="checkerDateFrom"
          with-time
          placeholder="От"
          button-class="w-full sm:w-[180px]"
          @change="loadChecker"
        />
        <BaseDatePicker
          v-model="checkerDateTo"
          with-time
          placeholder="До"
          button-class="w-full sm:w-[180px]"
          @change="loadChecker"
        />
        <BaseSelect
          :model-value="checkerGranularity"
          :options="granularityOptions"
          class="w-[130px]"
          @update:modelValue="(v: string) => { checkerGranularity = v as Granularity; loadChecker() }"
        />
      </div>

      <!-- Stats Cards -->
      <div class="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard
          v-for="card in checkerCards"
          :key="card.label"
          :label="card.label"
          :value="card.value"
          :loading="loadingChecker"
        />
      </div>

      <!-- Timeseries chart -->
      <div class="mb-6 rounded-xl bg-bg-surface p-3">
        <BaseLineChart
          :series="checkerSeries"
          :x-labels="checkerXLabels"
          :format-value="formatInt"
          :loading="loadingChecker"
          empty-text="Нет данных за выбранный период"
        />
      </div>

      <!-- Per-checker (provider) table -->
      <div class="mb-6">
        <h3 class="mb-3 px-1 text-sm font-bold text-text-main">Чекеры</h3>
        <DataTable
          :columns="checkerProviderCols"
          :rows="checkerStats?.providers ?? []"
          :loading="loadingChecker"
          row-key="provider_id"
          :show-pagination="false"
          empty-text="Нет данных за выбранный период"
        >
          <template #cell-total="{ value }">{{ formatInt(value as number) }}</template>
          <template #cell-success="{ value }">{{ formatInt(value as number) }}</template>
          <template #cell-failed="{ value }">{{ formatInt(value as number) }}</template>
          <template #cell-manual="{ value }">{{ formatInt(value as number) }}</template>
          <template #cell-auto="{ value }">{{ formatInt(value as number) }}</template>
          <template #cell-clean="{ value }">{{ formatInt(value as number) }}</template>
          <template #cell-suspicious="{ value }">{{ formatInt(value as number) }}</template>
          <template #cell-suspicious_rate="{ value }">{{ formatPercent(value as number) }}</template>
          <template #cell-spent_usdt="{ value }">{{ formatAmount(value as number) }}</template>
          <template #cell-refunded_usdt="{ value }">{{ formatAmount(value as number) }}</template>
          <template #cell-net_usdt="{ value }">{{ formatAmount(value as number) }}</template>
          <template #cell-avg_price_usdt="{ value }">{{ formatAmount(value as number) }}</template>
        </DataTable>
      </div>

      <!-- Top traders table -->
      <div>
        <h3 class="mb-3 px-1 text-sm font-bold text-text-main">Топ трейдеров</h3>
        <DataTable
          :columns="checkerTraderCols"
          :rows="checkerStats?.top_traders ?? []"
          :loading="loadingChecker"
          row-key="trader_user_id"
          :show-pagination="false"
          empty-text="Нет данных за выбранный период"
        >
          <template #cell-username="{ value }">{{ (value as string | null) ?? '—' }}</template>
          <template #cell-checks="{ value }">{{ formatInt(value as number) }}</template>
          <template #cell-spent_usdt="{ value }">{{ formatAmount(value as number) }}</template>
          <template #cell-suspicious_rate="{ value }">{{ formatPercent(value as number) }}</template>
        </DataTable>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import dayjs from 'dayjs'
import PageHeader from '@/components/layout/PageHeader.vue'
import StatCard from '@/components/ui/StatCard.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseDatePicker from '@/components/ui/BaseDatePicker.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import ActivityRangeChart from '@/components/ui/ActivityRangeChart.vue'
import MerchantFunnelChart from '@/components/ui/MerchantFunnelChart.vue'
import BaseLineChart from '@/components/ui/BaseLineChart.vue'
import {
  statsService,
  CHECK_SIZE_LABELS,
  type AdminStats,
  type AdminActivity,
  type VolumeDistribution,
  type MerchantStatsRow,
  type MerchantTimeseries,
  type CheckerStats,
  type CheckerTimeseries,
} from '@/api/services/stats.service'
import { useToast } from '@/composables/useToast'
import { formatDate, formatAmount, formatInt, formatPercent } from '@/utils/format'
import { getUserTz, toUnixTs } from '@/utils/datetime'
import { TRADER_LEGEND } from '@/utils/activityColor'
import { BarChart3, Target, TrendingUp, Wallet } from 'lucide-vue-next'

const toast = useToast()
const traderLegend = TRADER_LEGEND

type Granularity = '' | '3hour' | 'hour' | 'day' | 'week' | 'month'
// «Активность» additionally offers a per-minute bucket (the snapshot cadence);
// the merchant funnel does not, so it gets its own, wider type.
type ActivityGranularity = Granularity | 'minute'

const tabs = [
  { key: 'requests', label: 'Запросы' },
  { key: 'activity', label: 'Активность' },
  { key: 'merchants', label: 'Мерчанты' },
  { key: 'checker', label: 'Чекер' },
]

const activeTab = ref('requests')

const loadingStats = ref(true)
const stats = ref<AdminStats | null>(null)

const statCards = computed(() => [
  { label: 'Запросы шт 24ч', value: formatInt(stats.value?.payin_requests_24h ?? 0), icon: BarChart3 },
  { label: 'Запросы шт', value: formatInt(stats.value?.payin_requests_total ?? 0), icon: Target },
  { label: 'Запросы руб 24ч', value: formatInt(stats.value?.requests_rub_24h ?? 0), icon: TrendingUp },
  { label: 'Запросы руб', value: formatInt(stats.value?.requests_rub ?? 0), icon: Wallet },
])

const loadingLogs = ref(false)
const logs = ref<any[]>([])
const page = ref(1)
const perPage = ref(50)
const totalItems = ref(0)

const totalPages = computed(() => Math.max(1, Math.ceil(totalItems.value / perPage.value)))

const columns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'merchant_login', label: 'Мерчант' },
  { key: 'amount_rub', label: 'Сумма (руб)' },
  { key: 'method', label: 'Метод' },
  { key: 'success', label: 'Статус' },
  { key: 'response_time_ms', label: 'Время обр.' },
  { key: 'created_at', label: 'Дата' },
]

async function loadStats() {
  loadingStats.value = true
  try {
    const { data } = await statsService.getAdminStats()
    stats.value = data
  } catch {
    toast.error('Ошибка загрузки статистики')
  } finally {
    loadingStats.value = false
  }
}

const loadingVolume = ref(false)
const volumeData = ref<VolumeDistribution[]>([])

const volumeCols: Column[] = [
  { key: 'method', label: 'Метод' },
  { key: 'lt_1000', label: '< 1000', align: 'right' },
  { key: 'from_1000', label: '1000-5000', align: 'right' },
  { key: 'from_5000', label: '5000-8000', align: 'right' },
  { key: 'from_8000', label: '8000-10000', align: 'right' },
  { key: 'from_10000', label: '10000-20000', align: 'right' },
  { key: 'from_20000', label: '20000+', align: 'right' },
  { key: 'total', label: 'Сумма', align: 'right' },
]

const VOLUME_BUCKET_KEYS: (keyof VolumeDistribution)[] = [
  'lt_1000',
  'from_1000',
  'from_5000',
  'from_8000',
  'from_10000',
  'from_20000',
]

const volumeRows = computed(() =>
  volumeData.value.map((row) => {
    const total = VOLUME_BUCKET_KEYS.reduce((sum, k) => sum + Number((row as any)[k] ?? 0), 0)
    return { ...row, total }
  }),
)

async function loadVolumeDistribution() {
  loadingVolume.value = true
  try {
    const { data } = await statsService.getVolumeDistribution()
    volumeData.value = data
  } catch {
    toast.error('Ошибка загрузки объемов')
  } finally {
    loadingVolume.value = false
  }
}

async function loadLogs() {
  loadingLogs.value = true
  try {
    const { data } = await statsService.getOrderRequests({
      page: page.value,
      limit: perPage.value,
    })
    logs.value = data.items
    totalItems.value = data.total
  } catch {
    toast.error('Ошибка загрузки запросов')
  } finally {
    loadingLogs.value = false
  }
}

// ── Активность tab ────────────────────────────────────────────────────────

const loadingActivity = ref(false)
const activity = ref<AdminActivity | null>(null)
const activityDateFrom = ref('')
const activityDateTo = ref('')
const activityGranularity = ref<ActivityGranularity>('')
const activityCurrency = ref('RUB')

const granularityOptions = [
  { value: '', label: 'Авто' },
  { value: 'hour', label: 'Часы' },
  { value: '3hour', label: '3 часа' },
  { value: 'day', label: 'Дни' },
  { value: 'week', label: 'Недели' },
  { value: 'month', label: 'Месяцы' },
]

// Activity supports a finer per-minute bucket (the snapshot cadence); the
// merchant funnel does not, so this list is activity-only.
const activityGranularityOptions = [
  { value: '', label: 'Авто' },
  { value: 'minute', label: 'Минуты' },
  ...granularityOptions.slice(1),
]

const currencyOptions = computed(() => {
  const list = activity.value?.available_currencies?.length
    ? activity.value.available_currencies
    : [activityCurrency.value]
  return list.map((c) => ({ value: c, label: c }))
})

function fmtBucket(unix: number, gran: string): string {
  const d = dayjs.unix(unix).tz(getUserTz())
  if (gran === 'minute' || gran === 'hour' || gran === '3hour') return d.format('DD.MM HH:mm')
  if (gran === 'month') return d.format('MM.YYYY')
  return d.format('DD.MM')
}

const activityXLabels = computed(() => {
  const gran = activity.value?.granularity ?? 'hour'
  return (activity.value?.buckets ?? []).map((b) => fmtBucket(b.ts, gran))
})

async function loadActivity() {
  loadingActivity.value = true
  try {
    const params: {
      date_from?: number
      date_to?: number
      granularity?: 'minute' | '3hour' | 'hour' | 'day' | 'week' | 'month'
      currency?: string
    } = { currency: activityCurrency.value }
    // Both pickers are with-time, so honour the exact chosen instant (no
    // endOfDay override, which would silently discard the «До» time).
    if (activityDateFrom.value) params.date_from = toUnixTs(activityDateFrom.value)
    if (activityDateTo.value) params.date_to = toUnixTs(activityDateTo.value)
    if (activityGranularity.value) params.granularity = activityGranularity.value
    const { data } = await statsService.getActivityTimeseries(params)
    activity.value = data
    if (data.currency) activityCurrency.value = data.currency
  } catch {
    toast.error('Ошибка загрузки активности')
  } finally {
    loadingActivity.value = false
  }
}

// ── Мерчанты tab ──────────────────────────────────────────────────────────

const loadingMerchants = ref(false)
const merchantRows = ref<MerchantStatsRow[]>([])
const merchantDateFrom = ref('')
const merchantDateTo = ref('')

const selectedMerchantId = ref<number | null>(null)
const merchantGranularity = ref<Granularity>('')
const loadingMerchantTs = ref(false)
const merchantTs = ref<MerchantTimeseries | null>(null)

const merchantColumns: Column[] = [
  { key: 'merchant', label: 'Мерчант' },
  { key: 'requests', label: 'Запросы', align: 'right' },
  { key: 'orders_created', label: 'Созданные', align: 'right' },
  { key: 'orders_success', label: 'Успешные', align: 'right' },
  { key: 'conversion_pct', label: 'Конверсия', align: 'right' },
  { key: 'payout_pct', label: 'Выдача', align: 'right' },
  ...CHECK_SIZE_LABELS.map((label, i) => ({ key: `b${i}`, label, align: 'right' as const })),
]

const merchantTableRows = computed(() =>
  merchantRows.value.map((r) => {
    const flat: Record<string, any> = {
      merchant_id: r.merchant_id,
      merchant_login: r.merchant_login,
      requests: r.requests,
      orders_created: r.orders_created,
      orders_success: r.orders_success,
      conversion_pct: r.conversion_pct,
      payout_pct: r.payout_pct,
    }
    r.check_size_buckets.forEach((v, i) => { flat[`b${i}`] = formatInt(v) })
    return flat
  }),
)

const merchantOptions = computed(() => [
  { value: '', label: 'Выберите мерчанта' },
  ...merchantRows.value.map((r) => ({
    value: String(r.merchant_id),
    label: r.merchant_login ? `${r.merchant_login} (ID ${r.merchant_id})` : `ID ${r.merchant_id}`,
  })),
])

function merchantRangeParams() {
  const params: { date_from?: number; date_to?: number } = {}
  if (merchantDateFrom.value) params.date_from = toUnixTs(merchantDateFrom.value)
  if (merchantDateTo.value) params.date_to = toUnixTs(merchantDateTo.value)
  return params
}

async function loadMerchants() {
  loadingMerchants.value = true
  try {
    const { data } = await statsService.getMerchantStats(merchantRangeParams())
    merchantRows.value = data
  } catch {
    toast.error('Ошибка загрузки статистики мерчантов')
  } finally {
    loadingMerchants.value = false
  }
}

async function loadMerchantTimeseries() {
  if (selectedMerchantId.value == null) return
  loadingMerchantTs.value = true
  try {
    const params: {
      merchant_id: number
      date_from?: number
      date_to?: number
      granularity?: '3hour' | 'hour' | 'day' | 'week' | 'month'
    } = { merchant_id: selectedMerchantId.value, ...merchantRangeParams() }
    if (merchantGranularity.value) params.granularity = merchantGranularity.value
    const { data } = await statsService.getMerchantTimeseries(params)
    merchantTs.value = data
  } catch {
    toast.error('Ошибка загрузки графика мерчанта')
  } finally {
    loadingMerchantTs.value = false
  }
}

function selectMerchant(id: number | null) {
  selectedMerchantId.value = id
  merchantTs.value = null
  if (id != null) loadMerchantTimeseries()
}

function onMerchantRangeChange() {
  loadMerchants()
  if (selectedMerchantId.value != null) loadMerchantTimeseries()
}

const merchantTsXLabels = computed(() => {
  const gran = merchantTs.value?.granularity ?? 'hour'
  return (merchantTs.value?.points ?? []).map((p) => fmtBucket(p.ts, gran))
})

// ── Чекер tab ─────────────────────────────────────────────────────────────

const loadingChecker = ref(false)
const checkerStats = ref<CheckerStats | null>(null)
const checkerTs = ref<CheckerTimeseries | null>(null)
const checkerDateFrom = ref('')
const checkerDateTo = ref('')
const checkerGranularity = ref<Granularity>('')

const checkerCards = computed(() => {
  const t = checkerStats.value?.totals
  return [
    { label: 'Всего проверок', value: formatInt(t?.total ?? 0) },
    { label: 'Подозрительных', value: formatPercent(t?.suspicious_rate ?? 0) },
    { label: 'Потрачено USDT', value: formatAmount(t?.spent_usdt ?? 0) },
  ]
})

const checkerProviderCols: Column[] = [
  { key: 'checker', label: 'Чекер' },
  { key: 'total', label: 'Всего', align: 'right' },
  { key: 'success', label: 'OK', align: 'right' },
  { key: 'failed', label: 'Ошибки', align: 'right' },
  { key: 'manual', label: 'Ручных', align: 'right' },
  { key: 'auto', label: 'Авто', align: 'right' },
  { key: 'clean', label: 'Чистых', align: 'right' },
  { key: 'suspicious', label: 'Подозр.', align: 'right' },
  { key: 'suspicious_rate', label: 'Доля подозр.', align: 'right' },
  { key: 'spent_usdt', label: 'Потрачено', align: 'right' },
  { key: 'refunded_usdt', label: 'Возвраты', align: 'right' },
  { key: 'net_usdt', label: 'Чисто', align: 'right' },
  { key: 'avg_price_usdt', label: 'Ср. цена', align: 'right' },
]

const checkerTraderCols: Column[] = [
  { key: 'username', label: 'Трейдер' },
  { key: 'checks', label: 'Проверок', align: 'right' },
  { key: 'spent_usdt', label: 'Потрачено', align: 'right' },
  { key: 'suspicious_rate', label: 'Подозр.', align: 'right' },
]

const checkerXLabels = computed(() => {
  const gran = checkerTs.value?.granularity ?? 'day'
  return (checkerTs.value?.points ?? []).map((p) => fmtBucket(p.ts, gran))
})

const checkerSeries = computed(() => [
  {
    name: 'Проверок',
    color: '#D6A38F', // tailwind accent.DEFAULT — no --color-* CSS custom properties exist in this app
    values: (checkerTs.value?.points ?? []).map((p) => p.checks),
    showArea: true,
  },
])

function checkerRangeParams() {
  const params: { date_from?: number; date_to?: number } = {}
  if (checkerDateFrom.value) params.date_from = toUnixTs(checkerDateFrom.value)
  if (checkerDateTo.value) params.date_to = toUnixTs(checkerDateTo.value)
  return params
}

async function loadChecker() {
  loadingChecker.value = true
  try {
    const range = checkerRangeParams()
    const tsParams: {
      date_from?: number
      date_to?: number
      granularity?: '3hour' | 'hour' | 'day' | 'week' | 'month'
    } = { ...range }
    if (checkerGranularity.value) tsParams.granularity = checkerGranularity.value
    const [statsRes, tsRes] = await Promise.all([
      statsService.getCheckerStats(range),
      statsService.getCheckerTimeseries(tsParams),
    ])
    checkerStats.value = statsRes.data
    checkerTs.value = tsRes.data
  } catch {
    toast.error('Ошибка загрузки статистики чекеров')
  } finally {
    loadingChecker.value = false
  }
}

// Lazily load a tab's data the first time it's opened.
watch(activeTab, (tab) => {
  if (tab === 'activity' && !activity.value) loadActivity()
  if (tab === 'merchants' && !merchantRows.value.length) loadMerchants()
  if (tab === 'checker' && !checkerStats.value) loadChecker()
})

onMounted(() => {
  loadStats()
  loadVolumeDistribution()
  loadLogs()
})
</script>
