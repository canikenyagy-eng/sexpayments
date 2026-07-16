<template>
  <div>
    <PageHeader :title="profile ? pageTitle : '\u00A0'" />

    <div v-if="loadingProfile" class="py-16"><LoadingSpinner /></div>

    <template v-else-if="profile">
      <!-- Tab navigation -->
      <div class="mb-6 flex gap-1 rounded-xl bg-bg-surface p-1">
        <button
          v-for="t in tabs"
          :key="t.key"
          class="rounded-lg px-4 py-2 text-sm font-bold transition"
          :class="activeTab === t.key ? 'bg-accent/15 text-accent' : 'text-text-muted hover:text-text-main'"
          @click="activeTab = t.key"
        >
          {{ t.label }}
        </button>
      </div>

      <!-- Overview tab -->
      <template v-if="activeTab === 'overview'">
        <div class="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
          <StatCard label="Баланс (WORK)" :value="`${formatAmount(profile.balance_work)} USDT`" :icon="Wallet" />
          <StatCard label="Заморожено (ESCROW)" :value="`${formatAmount(profile.balance_escrow)} USDT`" :icon="Lock" />
          <StatCard label="Статус" :value="statusLabel" :icon="Activity" />
          <StatCard label="Валюта" :value="profile.currency" :icon="ArrowRightLeft" />
        </div>

        <div class="mb-6 flex items-center gap-2">
          <BaseDatePicker v-model="dateFrom" with-time placeholder="От" button-class="w-[190px]" @change="loadStats" />
          <BaseDatePicker v-model="dateTo" with-time placeholder="До" button-class="w-[190px]" @change="loadStats" />
        </div>

        <div class="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
          <StatCard label="Оборот USDT" :value="formatInt(stats.turnover_usdt)" :icon="TrendingUp" />
          <StatCard label="Комиссии USDT" :value="formatInt(stats.fee_usdt)" :icon="Receipt" />
          <StatCard label="Ордеров всего" :value="stats.orders_total" :icon="Package" />
          <StatCard label="Успешных" :value="stats.orders_success" :icon="CheckCircle2" />
          <StatCard label="Конверсия" :value="Math.round(stats.conversion_pct) + '%'" :icon="Target" />
          <StatCard label="Активных" :value="stats.orders_active" :icon="Zap" />
          <StatCard label="Неудачных" :value="stats.orders_failed" :icon="XCircle" />
          <StatCard label="Выводы (ожид.)" :value="stats.pending_withdrawals" :icon="Hourglass" />
          <StatCard label="Споры" :value="stats.active_disputes" :icon="AlertTriangle" />
        </div>

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
            <template #cell-amount="{ row }">
              {{ formatAmount(row.amount) }} {{ row.currency }}
            </template>
            <template #cell-payment_method="{ value }">
              <MethodBadge :method="value" />
            </template>
            <template #cell-status="{ value }">
              <StatusBadge :status="value" />
            </template>
            <template #cell-created_at="{ value }">
              {{ formatDate(value) }}
            </template>
          </DataTable>
          <div class="mt-3 text-right">
            <router-link
              :to="{ path: '/merchant/orders', query: { merchant_id: String(terminalId) } }"
              class="text-sm font-bold text-accent hover:underline"
            >
              Все ордера →
            </router-link>
          </div>
        </BaseCard>
      </template>

      <!-- Settings tab -->
      <template v-if="activeTab === 'settings'">
        <div class="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <BaseCard title="Основные настройки">
            <div class="space-y-4">
              <BaseInput v-model="form.name" label="Название терминала" placeholder="Мой магазин" />
              <BaseInput v-model="form.webhook_url" label="Webhook URL" placeholder="https://example.com/webhook" />
              <BaseInput v-model="form.order_ttl_seconds" label="Время на сделку (секунды)" type="number" />
              <BaseInput v-model="form.requisite_search_timeout_ms" label="Таймаут поиска реквизита (мс)" type="number" />
              <BaseButton variant="gold" :loading="saving" @click="saveSettings">Сохранить</BaseButton>
            </div>
          </BaseCard>

          <BaseCard title="Информация">
            <div class="space-y-2 text-sm">
              <div class="flex justify-between">
                <span class="text-text-muted">ID терминала</span>
                <span class="font-mono text-text-main font-semibold">{{ profile.id }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-text-muted">Статус</span>
                <StatusBadge :status="profile.status" context="merchant" />
              </div>
              <div class="flex justify-between">
                <span class="text-text-muted">Валюта</span>
                <span class="text-text-main font-semibold">{{ profile.currency }}</span>
              </div>
              <div class="flex justify-between">
                <span class="text-text-muted">Фикс. комиссия вывода</span>
                <span class="text-text-main font-semibold">{{ profile.withdrawal_fee_fixed }} USDT</span>
              </div>
            </div>
          </BaseCard>

          <BaseCard title="API-ключ">
            <div class="space-y-4">
              <div>
                <label class="mb-1.5 block text-sm font-semibold text-text-secondary">Текущий ключ</label>
                <div class="input-field font-mono text-sm">{{ profile.api_key_masked }}</div>
              </div>
              <div class="rounded-lg border border-status-danger/20 bg-status-danger/5 p-3">
                <p class="mb-2 text-sm text-status-danger font-semibold">Перегенерация ключа</p>
                <p class="mb-3 text-xs text-text-muted">
                  Старый ключ перестанет работать. Новый секрет показывается только один раз.
                </p>
                <BaseButton variant="danger" size="sm" :loading="resetting" @click="confirmResetKey">
                  Перегенерировать
                </BaseButton>
              </div>

              <div v-if="newKey" class="rounded-lg bg-bg-card p-3 space-y-2">
                <p class="text-sm font-bold text-accent">Новые ключи (сохраните!):</p>
                <div>
                  <label class="text-xs text-text-muted">API Key:</label>
                  <div class="flex items-center gap-2">
                    <code class="flex-1 rounded bg-bg-surface px-2 py-1 font-mono text-xs text-text-main break-all">{{ newKey.api_key }}</code>
                    <button class="text-text-muted hover:text-accent" @click="copy(newKey!.api_key)"><Copy class="h-4 w-4" /></button>
                  </div>
                </div>
                <div>
                  <label class="text-xs text-text-muted">API Secret:</label>
                  <div class="flex items-center gap-2">
                    <code class="flex-1 rounded bg-bg-surface px-2 py-1 font-mono text-xs text-text-main break-all">{{ newKey.api_secret }}</code>
                    <button class="text-text-muted hover:text-accent" @click="copy(newKey!.api_secret)"><Copy class="h-4 w-4" /></button>
                  </div>
                </div>
              </div>
            </div>
          </BaseCard>

          <BaseCard title="Платёжные методы и комиссии" class="lg:col-span-2">
            <div v-if="!methodRows.length" class="py-4 text-center text-sm text-text-muted">
              Нет доступных методов оплаты
            </div>
            <div v-else class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              <div v-for="pm in methodRows" :key="pm.method" class="rounded-xl bg-bg-card p-4">
                <div class="mb-2 flex items-center justify-between">
                  <MethodBadge :method="pm.method" />
                  <span class="text-sm font-bold text-text-secondary">{{ pm.fee_percentage }}%</span>
                </div>
              </div>
            </div>
          </BaseCard>
        </div>
      </template>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import PageHeader from '@/components/layout/PageHeader.vue'
import StatCard from '@/components/ui/StatCard.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseDatePicker from '@/components/ui/BaseDatePicker.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import { Copy } from 'lucide-vue-next'
import { merchantsService } from '@/api/services/merchants.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { formatAmount, formatInt, formatDate, copyToClipboard } from '@/utils/format'
import { toUnixTs } from '@/utils/datetime'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import type { MerchantFullProfile, MerchantStats, Order, PaymentMethod } from '@/types'
import { ALL_PAYMENT_METHODS, merchantStatusLabels } from '@/constants'
import {
  Wallet, Lock, Activity, ArrowRightLeft, TrendingUp, Receipt,
  Package, CheckCircle2, Target, Zap, XCircle, Hourglass, AlertTriangle,
} from 'lucide-vue-next'

const route = useRoute()
const toast = useToast()
const { confirm: confirmDialog } = useConfirm()

const terminalId = computed(() => Number(route.params.id))
const activeTab = ref<'overview' | 'settings'>('overview')

const tabs = [
  { key: 'overview' as const, label: 'Обзор' },
  { key: 'settings' as const, label: 'Настройки' },
]

const loadingProfile = ref(true)
const loadingOrders = ref(true)
const saving = ref(false)
const resetting = ref(false)

const dateFrom = ref('')
const dateTo = ref('')

const profile = ref<MerchantFullProfile | null>(null)
const stats = ref<MerchantStats>({
  turnover_usdt: 0, fee_usdt: 0,
  orders_total: 0, orders_success: 0, orders_active: 0, orders_failed: 0,
  conversion_pct: 0, pending_withdrawals: 0, active_disputes: 0,
})
const recentOrders = ref<Order[]>([])
const newKey = ref<{ api_key: string; api_secret: string } | null>(null)

const form = reactive({
  name: '',
  webhook_url: '',
  order_ttl_seconds: '',
  requisite_search_timeout_ms: '',
})

const pageTitle = computed(() =>
  profile.value
    ? profile.value.name || `Терминал #${profile.value.id}`
    : `Терминал #${terminalId.value}`,
)

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
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Дата' },
]

function copy(text: string) {
  copyToClipboard(text).then(
    () => toast.success('Скопировано'),
    () => toast.error('Не удалось скопировать'),
  )
}

async function loadProfile() {
  loadingProfile.value = true
  try {
    const { data } = await merchantsService.getMyProfile(terminalId.value)
    profile.value = data
    form.name = data.name ?? ''
    form.webhook_url = data.webhook_url ?? ''
    form.order_ttl_seconds = String(data.order_ttl_seconds)
    form.requisite_search_timeout_ms = String(data.requisite_search_timeout_ms)
  } catch {
    toast.error('Ошибка загрузки терминала')
  } finally {
    loadingProfile.value = false
  }
}

async function loadStats() {
  try {
    const params: Record<string, number> = { merchant_id: terminalId.value }
    if (dateFrom.value) params.date_from = toUnixTs(dateFrom.value)
    if (dateTo.value) params.date_to = toUnixTs(dateTo.value, { endOfDay: true })
    const { data } = await merchantsService.getMyStats(params)
    stats.value = data
  } catch {
    toast.error('Ошибка загрузки статистики')
  }
}

async function loadOrders() {
  loadingOrders.value = true
  try {
    const { data } = await merchantsService.listMyOrders({ limit: 10, merchant_id: terminalId.value })
    recentOrders.value = data.items
  } catch {
    toast.error('Ошибка загрузки ордеров')
  } finally {
    loadingOrders.value = false
  }
}

async function saveSettings() {
  saving.value = true
  try {
    const updates: Record<string, any> = {}
    const p = profile.value

    if (form.name !== (p?.name ?? '')) {
      updates.name = form.name || null
    }
    if (form.webhook_url !== (p?.webhook_url ?? '')) {
      updates.webhook_url = form.webhook_url || null
    }
    if (form.order_ttl_seconds && Number(form.order_ttl_seconds) !== p?.order_ttl_seconds) {
      updates.order_ttl_seconds = Number(form.order_ttl_seconds)
    }
    if (form.requisite_search_timeout_ms && Number(form.requisite_search_timeout_ms) !== p?.requisite_search_timeout_ms) {
      updates.requisite_search_timeout_ms = Number(form.requisite_search_timeout_ms)
    }

    if (!Object.keys(updates).length) {
      toast.info('Нет изменений')
      saving.value = false
      return
    }

    const { data } = await merchantsService.updateSettings(updates, terminalId.value)
    profile.value = data
    toast.success('Настройки сохранены')
  } catch {
    toast.error('Ошибка сохранения настроек')
  } finally {
    saving.value = false
  }
}

async function confirmResetKey() {
  const yes = await confirmDialog('Вы уверены? Текущий API-ключ будет аннулирован.')
  if (!yes) return
  resetting.value = true
  try {
    const { data } = await merchantsService.resetMyApiKey(terminalId.value)
    newKey.value = data
    toast.success('API-ключ перегенерирован')
    loadProfile()
  } catch {
    toast.error('Ошибка перегенерации ключа')
  } finally {
    resetting.value = false
  }
}

watch(terminalId, () => {
  newKey.value = null
  loadProfile()
  loadStats()
  loadOrders()
})

onMounted(() => {
  loadProfile()
  loadStats()
  loadOrders()
})
</script>
