<template>
  <div>
    <PageHeader title="Dashboard">
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
    </PageHeader>

    <!-- 12 Stat Cards -->
    <div class="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
      <StatCard
        v-for="card in statCards"
        :key="card.label"
        :label="card.label"
        :value="card.value"
        :icon="card.icon"
        :loading="loadingStats"
      />
    </div>

    <!-- Quick Actions + Active Withdrawals -->
    <div class="mb-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
      <BaseCard title="Быстрые действия">
        <div class="space-y-3">
          <BaseButton variant="gold" class="w-full" @click="showCreateUser = true">
            Создание пользователя
          </BaseButton>
          <BaseButton variant="gold" class="w-full" @click="showHashDeposit = true">
            Пополнение хэшем
          </BaseButton>
        </div>
      </BaseCard>

      <div class="lg:col-span-2">
        <BaseCard title="Активные выводы">
          <DataTable
            :columns="withdrawalCols"
            :rows="pendingWithdrawals"
            row-key="id"
            :loading="loadingW"
          >
            <template #cell-amount="{ row }">
              {{ formatAmount(row.amount) }} {{ row.currency }}
            </template>
            <template #cell-status="{ value }">
              <StatusBadge :status="value" />
            </template>
          </DataTable>
        </BaseCard>
      </div>
    </div>

    <!-- Turnover & profit chart -->
    <BaseCard class="mb-6">
      <template #header>
        <div class="flex w-full flex-wrap items-center justify-between gap-2">
          <h3 class="text-base font-bold text-text-main">Оборот и прибыль</h3>
          <BaseSelect
            v-model="chartGranularity"
            :options="granularityOptions"
            class="w-[140px]"
            @update:modelValue="loadChart"
          />
        </div>
      </template>
      <RevenueChart
        :points="timeseries.points"
        :granularity="timeseries.granularity"
        :loading="loadingChart"
      />
    </BaseCard>

    <!-- Recent Deals -->
    <BaseCard title="Последние сделки">
      <DataTable
        :columns="orderCols"
        :rows="recentOrderRows"
        row-key="id"
        :loading="loadingO"
      >
        <template #cell-uuid="{ value }">
          <UuidDisplay :value="String(value)" show-icon />
        </template>
        <template #cell-amount="{ row }">
          {{ formatAmount(row.amount) }} {{ row.currency }}
        </template>
        <template #cell-status="{ value }">
          <StatusBadge :status="value" />
        </template>
        <template #cell-trader_login="{ value }">
          <span class="text-xs">{{ value }}</span>
        </template>
        <template #cell-merchant_login="{ value }">
          <span class="text-xs">{{ value }}</span>
        </template>
        <template #cell-created_at="{ value }">
          {{ formatDate(value) }}
        </template>
      </DataTable>
    </BaseCard>

    <CreateUserModal v-model="showCreateUser" />
    <HashDepositModal :open="showHashDeposit" @update:open="showHashDeposit = $event" />
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import StatCard from '@/components/ui/StatCard.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseDatePicker from '@/components/ui/BaseDatePicker.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import DataTable from '@/components/ui/DataTable.vue'
import CreateUserModal from '@/components/modals/CreateUserModal.vue'
import HashDepositModal from '@/components/modals/HashDepositModal.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { statsService, type AdminStats, type AdminTimeseries } from '@/api/services/stats.service'
import RevenueChart from '@/components/ui/RevenueChart.vue'
import { ordersService } from '@/api/services/orders.service'
import { financesService } from '@/api/services/finances.service'
import { usersService } from '@/api/services/users.service'
import { merchantsService } from '@/api/services/merchants.service'
import { useToast } from '@/composables/useToast'
import { formatInt, formatAmount, formatDate } from '@/utils/format'
import { toUnixTs } from '@/utils/datetime'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import type { Order, WithdrawalRequest } from '@/types'
import { Wallet, TrendingUp, BarChart3, CreditCard, Package, CheckCircle2, Zap, Target, Store, Users, Hourglass, AlertTriangle } from 'lucide-vue-next'

const toast = useToast()

const dateFrom = ref('')
const dateTo = ref('')

const stats = ref<AdminStats>({
  turnover_usdt: 0, profit_usdt: 0, requests_rub: 0, requests_rub_24h: 0, payout_pct: 0, payin_requests_total: 0, payin_requests_24h: 0,
  orders_total: 0, orders_success: 0, orders_active: 0, conversion_pct: 0,
  merchants_online_24h: 0, traders_online_24h: 0, pending_withdrawals: 0, active_disputes: 0,
})
const loadingStats = ref(true)
const recentOrders = ref<Order[]>([])
const pendingWithdrawals = ref<WithdrawalRequest[]>([])
const timeseries = ref<AdminTimeseries>({ granularity: 'day', points: [] })
const loadingChart = ref(true)
const chartGranularity = ref<'auto' | '3hour' | 'hour' | 'day' | 'week' | 'month'>('auto')

const granularityOptions = [
  { value: 'auto', label: 'Авто' },
  { value: 'hour', label: 'Часы' },
  { value: '3hour', label: '3 часа' },
  { value: 'day', label: 'Дни' },
  { value: 'week', label: 'Недели' },
  { value: 'month', label: 'Месяцы' },
]
const usersMap = ref<Record<number, string>>({})
const merchantsMap = ref<Record<number, { user_id: number; name?: string }>>({})
const loadingO = ref(true)
const loadingW = ref(true)

const showCreateUser = ref(false)
const showHashDeposit = ref(false)

const statCards = computed(() => [
  { label: 'Оборот USDT', value: formatInt(stats.value.turnover_usdt), icon: Wallet },
  { label: 'Прибыль USDT', value: formatInt(stats.value.profit_usdt), icon: TrendingUp },
  { label: 'Запросов RUB', value: formatInt(stats.value.requests_rub), icon: BarChart3 },
  { label: 'Выдача %', value: Math.round(stats.value.payout_pct) + '%', icon: CreditCard },
  { label: 'Сделок всего', value: stats.value.orders_total, icon: Package },
  { label: 'Сделок успешных', value: stats.value.orders_success, icon: CheckCircle2 },
  { label: 'Сделок активных', value: stats.value.orders_active, icon: Zap },
  { label: 'Конверсия %', value: Math.round(stats.value.conversion_pct) + '%', icon: Target },
  { label: 'Мерчанты 24ч', value: stats.value.merchants_online_24h, icon: Store },
  { label: 'Трейдеры 24ч', value: stats.value.traders_online_24h, icon: Users },
  { label: 'Выводы (pending)', value: stats.value.pending_withdrawals, icon: Hourglass },
  { label: 'Активные споры', value: stats.value.active_disputes, icon: AlertTriangle },
])

const withdrawalCols: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'user_role', label: 'Роль' },
  { key: 'destination_address', label: 'Адрес' },
  { key: 'status', label: 'Статус' },
]

const orderCols: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'uuid', label: 'UUID' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'status', label: 'Статус' },
  { key: 'trader_login', label: 'Трейдер' },
  { key: 'merchant_login', label: 'Мерчант' },
  { key: 'created_at', label: 'Создана' },
]

function getMerchantLogin(merchantId: number): string {
  const m = merchantsMap.value[merchantId]
  if (!m) return `#${merchantId}`
  return usersMap.value[m.user_id] ?? m.name ?? `#${merchantId}`
}

function getTraderLogin(traderId?: number | null): string {
  if (!traderId) return '—'
  return usersMap.value[traderId] ?? `#${traderId}`
}

const recentOrderRows = computed(() =>
  recentOrders.value.map(o => ({
    ...o,
    trader_login: getTraderLogin(o.trader_id),
    merchant_login: getMerchantLogin(o.merchant_id),
  })),
)

async function loadStats() {
  loadingStats.value = true
  try {
    const params: Record<string, number> = {}
    if (dateFrom.value) params.date_from = toUnixTs(dateFrom.value)
    if (dateTo.value) params.date_to = toUnixTs(dateTo.value, { endOfDay: true })
    const { data } = await statsService.getAdminStats(params)
    stats.value = data
  } catch {
    toast.error('Ошибка загрузки статистики')
  } finally {
    loadingStats.value = false
  }
  loadChart()
}

async function loadChart() {
  loadingChart.value = true
  try {
    const params: Record<string, any> = {}
    if (dateFrom.value) params.date_from = toUnixTs(dateFrom.value)
    if (dateTo.value) params.date_to = toUnixTs(dateTo.value, { endOfDay: true })
    if (chartGranularity.value !== 'auto') params.granularity = chartGranularity.value
    const { data } = await statsService.getAdminTimeseries(params)
    timeseries.value = data
  } catch {
    toast.error('Ошибка загрузки графика')
  } finally {
    loadingChart.value = false
  }
}

async function loadOrders() {
  loadingO.value = true
  try {
    const [ordersRes, usersRes, merchRes] = await Promise.all([
      ordersService.list({ limit: 15 }),
      usersService.list({ limit: 500 }),
      merchantsService.listAll({ limit: 500 }),
    ])
    recentOrders.value = ordersRes.data.items
    const uMap: Record<number, string> = {}
    for (const u of usersRes.data) uMap[u.id] = u.username
    usersMap.value = uMap
    const mMap: Record<number, { user_id: number; name?: string }> = {}
    for (const m of merchRes.data) mMap[m.id] = { user_id: m.user_id, name: m.name }
    merchantsMap.value = mMap
  } catch {
    toast.error('Ошибка загрузки ордеров')
  } finally {
    loadingO.value = false
  }
}

async function loadWithdrawals() {
  loadingW.value = true
  try {
    const { data } = await financesService.listWithdrawals({ status: 'pending', limit: 10 })
    pendingWithdrawals.value = data
  } catch {
    toast.error('Ошибка загрузки выводов')
  } finally {
    loadingW.value = false
  }
}

onMounted(() => {
  loadStats()
  loadOrders()
  loadWithdrawals()
})
</script>
