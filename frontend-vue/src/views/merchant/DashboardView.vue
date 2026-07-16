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

    <div v-if="loadingProfile" class="py-16"><LoadingSpinner /></div>

    <template v-else-if="profile">
      <!-- Balance cards — aggregated across every terminal the merchant owns. -->
      <div class="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
        <StatCard
          label="Общий баланс (WORK)"
          :value="`${formatAmount(totalWorkUsdt)} USDT`"
          :icon="Wallet"
          :subtitle="terminalCount > 0 ? `По ${terminalCount} ${pluralize(terminalCount, ['терминалу', 'терминалам', 'терминалам'])}` : undefined"
        />
        <StatCard
          label="Заморожено (ESCROW)"
          :value="`${formatAmount(totalEscrowUsdt)} USDT`"
          :icon="Lock"
        />
        <StatCard label="Статус" :value="statusLabel" :icon="Activity" />
        <StatCard
          label="Терминалов"
          :value="String(terminalCount)"
          :icon="ArrowRightLeft"
        />
      </div>

      <div class="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
        <StatCard label="Оборот USDT" :value="formatInt(stats.turnover_usdt)" :icon="TrendingUp" :loading="loadingStats" />
        <StatCard label="Комиссии USDT" :value="formatInt(stats.fee_usdt)" :icon="Receipt" :loading="loadingStats" />
        <StatCard label="Ордеров всего" :value="stats.orders_total" :icon="Package" :loading="loadingStats" />
        <StatCard label="Успешных" :value="stats.orders_success" :icon="CheckCircle2" :loading="loadingStats" />
        <StatCard label="Конверсия" :value="Math.round(stats.conversion_pct) + '%'" :icon="Target" :loading="loadingStats" />
        <StatCard label="Активных" :value="stats.orders_active" :icon="Zap" :loading="loadingStats" />
        <StatCard label="Неудачных" :value="stats.orders_failed" :icon="XCircle" :loading="loadingStats" />
        <StatCard label="Выводы (ожид.)" :value="stats.pending_withdrawals" :icon="Hourglass" :loading="loadingStats" />
        <StatCard label="Споры" :value="stats.active_disputes" :icon="AlertTriangle" :loading="loadingStats" />
      </div>

      <!-- System status -->
      <div class="mb-6 grid grid-cols-1 gap-6 lg:grid-cols-3">
        <BaseCard title="Платёжные методы">
          <div v-if="!methodRows.length" class="py-4 text-center text-sm text-text-muted">
            Нет доступных методов
          </div>
          <div v-else class="space-y-2">
            <div
              v-for="pm in methodRows"
              :key="pm.method"
              class="flex items-center justify-between rounded-lg bg-bg-card px-3 py-2"
            >
              <div class="flex items-center gap-2">
                <MethodBadge :method="pm.method" />
              </div>
              <span class="text-sm font-bold text-text-secondary">{{ pm.fee_percentage }}%</span>
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
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import StatCard from '@/components/ui/StatCard.vue'
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
import {
  Wallet, Lock, Activity, ArrowRightLeft, TrendingUp, Receipt,
  Package, CheckCircle2, Target, Zap, XCircle, Hourglass, AlertTriangle,
} from 'lucide-vue-next'

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
