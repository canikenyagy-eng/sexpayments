<template>
  <div>
    <AccountHero
      role="trader"
      eyebrow="Кабинет трейдера"
      title="Рабочий контур для активных сделок"
      subtitle="Управляйте входом, выходом, лимитами и активными ордерами в одном спокойном операционном экране."
      :metrics="heroMetrics"
    />

    <div v-if="loading" class="py-16"><LoadingSpinner /></div>

    <template v-else-if="trader">
      <div class="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard label="Статус" :value="traderStatusLabel" :icon="BarChart3" />

        <!-- Payin toggle card -->
        <div class="rounded-[22px] border border-accent/15 bg-bg-surface/70 p-4 shadow-prime backdrop-blur-xl">
          <span class="mb-3 block text-xs font-bold uppercase tracking-wider text-text-muted">Вход</span>
          <div class="flex items-center justify-between">
            <span class="text-sm font-bold" :class="trader.is_payin_active ? 'text-status-success' : 'text-text-muted'">
              {{ trader.is_payin_active ? 'Активен' : 'Неактивен' }}
            </span>
            <button
              type="button"
              :disabled="toggling"
              class="relative h-7 w-12 rounded-full border transition-colors duration-200 focus:outline-none disabled:opacity-60"
              :class="trader.is_payin_active ? 'border-accent/30 bg-accent-dark' : 'border-border bg-bg-hover'"
              @click="togglePayin"
            >
              <span
                class="absolute left-0.5 top-0.5 h-6 w-6 rounded-full bg-text-main shadow transition-transform duration-200"
                :class="trader.is_payin_active ? 'translate-x-5' : 'translate-x-0'"
              />
            </button>
          </div>
        </div>

        <!-- Payout toggle card -->
        <div class="rounded-[22px] border border-accent/15 bg-bg-surface/70 p-4 shadow-prime backdrop-blur-xl">
          <span class="mb-3 block text-xs font-bold uppercase tracking-wider text-text-muted">Выход</span>
          <div class="flex items-center justify-between">
            <span class="text-sm font-bold" :class="trader.is_payout_active ? 'text-status-success' : 'text-text-muted'">
              {{ trader.is_payout_active ? 'Активен' : 'Неактивен' }}
            </span>
            <button
              type="button"
              :disabled="toggling"
              class="relative h-7 w-12 rounded-full border transition-colors duration-200 focus:outline-none disabled:opacity-60"
              :class="trader.is_payout_active ? 'border-accent/30 bg-accent-dark' : 'border-border bg-bg-hover'"
              @click="togglePayout"
            >
              <span
                class="absolute left-0.5 top-0.5 h-6 w-6 rounded-full bg-text-main shadow transition-transform duration-200"
                :class="trader.is_payout_active ? 'translate-x-5' : 'translate-x-0'"
              />
            </button>
          </div>
        </div>

        <StatCard label="Активные ордера" :value="activeOrders.length" :icon="Package" />
      </div>

      <!-- Achievements: adaptive widgets only (one cell per tier/streak rule) -->
      <div
        v-if="achievements && achievements.enabled && (achievements.tiers.length || achievements.streaks.length)"
        class="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
      >
        <TierProgressBar v-for="(t, i) in achievements.tiers" :key="'tier-' + i" :tier="t" />
        <StreakRing v-for="(s, i) in achievements.streaks" :key="'streak-' + i" :streak="s" />
      </div>

      <BaseCard title="Конфигурация методов" class="mb-6">
        <div v-if="Object.keys(trader.methods_config || {}).length" class="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div v-for="(cfg, method) in trader.methods_config" :key="method" class="rounded-[22px] border border-accent/10 bg-bg-main/45 p-4">
            <MethodBadge :method="method" class="mb-2" />
            <div class="space-y-1 text-sm text-text-secondary">
              <div>Комиссия: <strong class="text-text-main">{{ cfg.fee }}%</strong></div>
              <div>Мин.: <strong class="text-text-main">{{ formatAmount(cfg.min_amount) }}</strong></div>
              <div>Макс.: <strong class="text-text-main">{{ formatAmount(cfg.max_amount) }}</strong></div>
            </div>
          </div>
        </div>
        <p v-else class="text-sm text-text-muted">Методы не настроены. Обратитесь к администратору.</p>
      </BaseCard>

      <BaseCard title="Активные ордера">
        <DataTable
          :columns="orderColumns"
          :rows="activeOrders"
          row-key="id"
          empty-text="Нет активных ордеров"
        >
          <template #cell-uuid="{ row }">
            <UuidDisplay :value="row.uuid" />
          </template>
          <template #cell-amount="{ row }">
            {{ row.amount }} {{ row.currency }}
          </template>
          <template #cell-status="{ row }">
            <StatusBadge :status="(row as Order).status" :expires-at="(row as Order).date_end" />
          </template>
          <template #cell-created_at="{ value }">
            {{ formatDate(value) }}
          </template>
          <template #actions="{ row }">
            <BaseButton
              v-if="['pending', 'receipt_uploaded'].includes(row.status)"
              action="accept"
              variant="success"
              size="sm"
              @click="confirmOrder(row as Order)"
            >
              Оплачено
            </BaseButton>
          </template>
        </DataTable>
      </BaseCard>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import AccountHero from '@/components/layout/AccountHero.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import StatCard from '@/components/ui/StatCard.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import TierProgressBar from '@/components/achievements/TierProgressBar.vue'
import StreakRing from '@/components/achievements/StreakRing.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import { tradersService } from '@/api/services/traders.service'
import { ordersService } from '@/api/services/orders.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useActiveStats } from '@/composables/useActiveStats'
import type { Trader, Order, TraderAchievements } from '@/types'
import { BarChart3, Package } from 'lucide-vue-next'
import { formatAmount, formatDate } from '@/utils/format'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'

const toast = useToast()
const { confirm: askConfirm } = useConfirm()
const { refresh: refreshActiveStats } = useActiveStats()

const loading = ref(true)
const toggling = ref(false)
const trader = ref<Trader | null>(null)
const activeOrders = ref<Order[]>([])
const achievements = ref<TraderAchievements | null>(null)

const traderStatusLabel = computed(() => {
  const map: Record<string, string> = { enabled: 'Включён', disabled: 'Выключен', blocked: 'Заблокирован' }
  return trader.value ? map[trader.value.status] ?? trader.value.status : '—'
})

const configuredMethods = computed(() => Object.keys(trader.value?.methods_config || {}).length)

const heroMetrics = computed(() => [
  {
    label: 'Активные ордера',
    value: activeOrders.value.length,
    caption: activeOrders.value.length ? 'Требуют внимания' : 'Очередь свободна',
  },
  {
    label: 'Вход',
    value: trader.value?.is_payin_active ? 'Активен' : 'Пауза',
    caption: trader.value?.is_payin_active ? 'Прием доступен' : 'Прием выключен',
  },
  {
    label: 'Выход',
    value: trader.value?.is_payout_active ? 'Активен' : 'Пауза',
    caption: trader.value?.is_payout_active ? 'Выплаты доступны' : 'Выплаты выключены',
  },
  {
    label: 'Методы',
    value: configuredMethods.value,
    caption: traderStatusLabel.value,
  },
])

const orderColumns: Column[] = [
  { key: 'uuid', label: 'UUID' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Создан' },
]

async function load() {
  loading.value = true
  try {
    const [traderRes, ordersRes, achRes] = await Promise.allSettled([
      tradersService.getMe(),
      ordersService.getMyActive(),
      tradersService.getMyAchievements(),
    ])
    if (traderRes.status === 'fulfilled') trader.value = traderRes.value.data
    if (ordersRes.status === 'fulfilled') activeOrders.value = ordersRes.value.data
    if (achRes.status === 'fulfilled') achievements.value = achRes.value.data
  } finally { loading.value = false }
}

async function togglePayin() {
  if (!trader.value) return
  toggling.value = true
  try {
    const { data } = await tradersService.togglePayin(!trader.value.is_payin_active)
    trader.value = data
    toast.success(data.is_payin_active ? 'Вход включён' : 'Вход выключен')
    refreshActiveStats()
  } catch { toast.error('Ошибка') }
  finally { toggling.value = false }
}

async function togglePayout() {
  if (!trader.value) return
  toggling.value = true
  try {
    const { data } = await tradersService.togglePayout(!trader.value.is_payout_active)
    trader.value = data
    toast.success(data.is_payout_active ? 'Выход включён' : 'Выход выключен')
  } catch { toast.error('Ошибка') }
  finally { toggling.value = false }
}

async function confirmOrder(order: Order) {
  const ok = await askConfirm({
    title: 'Подтвердить оплату',
    message: `Подтвердить получение оплаты по ордеру на ${formatAmount(order.amount)} ${order.currency}?`,
    confirmText: 'Оплачено',
    cancelText: 'Отмена',
    variant: 'success',
  })
  if (!ok) return
  try {
    await ordersService.confirmSuccess(order.id)
    toast.success('Ордер подтверждён')
    load()
  } catch { toast.error('Ошибка подтверждения') }
}

onMounted(load)
</script>
