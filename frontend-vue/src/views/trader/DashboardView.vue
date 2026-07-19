<template>
  <div class="trader-terminal space-y-6">
    <section class="relative overflow-hidden rounded-[1.4rem] border border-accent/20 bg-[#121010]/90 p-5 shadow-[0_30px_110px_rgba(0,0,0,0.48),inset_0_1px_0_rgba(245,245,245,0.04)] sm:p-7 lg:p-8">
      <div class="pointer-events-none absolute inset-0 terminal-grid opacity-45" />
      <div class="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/45 to-transparent" />
      <div class="pointer-events-none absolute inset-y-0 right-0 w-1/3 bg-gradient-to-l from-accent-dark/20 to-transparent" />

      <div class="relative grid gap-8 xl:grid-cols-[minmax(0,1fr)_420px] xl:items-stretch">
        <div class="flex min-h-[360px] flex-col justify-between">
          <div>
            <div class="mb-6 inline-flex max-w-full items-center gap-2 overflow-hidden rounded-xl border border-accent/20 bg-bg-main/60 px-3 py-2 text-[10px] font-black uppercase tracking-[0.12em] text-accent sm:text-xs sm:tracking-[0.18em]">
              <Activity class="h-4 w-4" />
              <span class="min-w-0 truncate">Рабочий терминал</span>
            </div>
            <h1 class="max-w-[660px] text-[clamp(2.35rem,4.35vw,4.7rem)] font-black leading-[0.92] text-text-main">
              Операционный терминал трейдера
            </h1>
            <p class="mt-5 max-w-2xl text-base font-semibold leading-7 text-text-secondary sm:text-lg">
              Активные сделки, лимиты, реквизиты и подтверждения собраны в одном рабочем контуре.
            </p>
          </div>

          <div class="mt-8 grid gap-3 sm:grid-cols-3">
            <div
              v-for="metric in terminalMetrics"
              :key="metric.label"
              class="rounded-[1rem] border border-accent/15 bg-bg-main/55 p-4 shadow-[inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl"
            >
              <p class="text-[11px] font-black uppercase tracking-[0.14em] text-text-muted">
                {{ metric.label }}
              </p>
              <p class="mt-3 break-words text-2xl font-black leading-none text-text-main">
                {{ metric.value }}
              </p>
              <p class="mt-2 text-xs font-semibold text-text-muted">
                {{ metric.caption }}
              </p>
            </div>
          </div>
        </div>

        <div class="relative rounded-[26px] border border-accent/20 bg-bg-surface/65 p-5 shadow-[inset_0_1px_0_rgba(245,245,245,0.04),0_22px_70px_rgba(0,0,0,0.34)] backdrop-blur-xl">
          <div class="mb-5 flex items-center justify-between">
            <div>
              <p class="text-xs font-black uppercase tracking-[0.16em] text-accent">Контур</p>
              <p class="mt-1 text-xl font-black text-text-main">Маршрутизация</p>
            </div>
            <span
              class="inline-flex items-center gap-2 rounded-full border px-3 py-1.5 text-xs font-black"
              :class="trader?.status === 'enabled' ? 'border-status-success/25 bg-status-success/10 text-status-success' : 'border-status-warning/25 bg-status-warning/10 text-status-warning'"
            >
              <span class="h-2 w-2 rounded-full bg-current" />
              {{ traderStatusLabel }}
            </span>
          </div>

          <div class="relative mb-5 aspect-[1.2] min-h-[260px] overflow-hidden rounded-[22px] border border-border bg-[#0D0D0D]/80">
            <div class="absolute inset-0 terminal-grid opacity-45" />
            <div class="absolute left-[13%] top-[18%] h-3 w-3 rounded-full bg-accent shadow-[0_0_28px_rgba(214,163,143,0.85)]" />
            <div class="absolute right-[16%] top-[28%] h-3 w-3 rounded-full bg-status-success shadow-[0_0_24px_rgba(76,175,80,0.6)]" />
            <div class="absolute bottom-[18%] left-[34%] h-3 w-3 rounded-full bg-accent-dark shadow-[0_0_30px_rgba(139,21,56,0.8)]" />
            <div class="payment-flow flow-a" />
            <div class="payment-flow flow-b" />
            <div class="payment-flow flow-c" />
            <div class="absolute inset-x-5 bottom-5 rounded-2xl border border-accent/15 bg-bg-main/70 p-4 backdrop-blur-xl">
              <div class="flex items-center justify-between gap-3">
                <div>
                  <p class="text-xs font-bold text-text-muted">Очередь сделок</p>
                  <p class="mt-1 text-2xl font-black text-text-main">{{ activeOrders.length }}</p>
                </div>
                <Gauge class="h-8 w-8 text-accent" />
              </div>
            </div>
          </div>

          <div class="grid gap-3 sm:grid-cols-2">
            <TerminalToggle label="Вход" :active="!!trader?.is_payin_active" :loading="toggling" @toggle="togglePayin" />
            <TerminalToggle label="Выход" :active="!!trader?.is_payout_active" :loading="toggling" @toggle="togglePayout" />
          </div>
        </div>
      </div>
    </section>

    <div v-if="loading" class="py-16"><LoadingSpinner /></div>

    <template v-else-if="trader">
      <section class="grid gap-4 xl:grid-cols-[minmax(0,1.2fr)_minmax(360px,0.8fr)]">
        <div class="rounded-[26px] border border-border bg-bg-surface/70 p-5 shadow-[0_24px_80px_rgba(0,0,0,0.28)] backdrop-blur-xl">
          <div class="mb-5 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p class="text-xs font-black uppercase tracking-[0.16em] text-accent">Сделки</p>
              <h2 class="mt-1 text-2xl font-black text-text-main">Активный поток</h2>
            </div>
            <RouterLink to="/trader/orders" class="text-sm font-black text-accent transition hover:text-accent-hover">
              Все сделки
            </RouterLink>
          </div>

          <div v-if="activeOrderPreview.length" class="space-y-2">
            <div
              v-for="order in activeOrderPreview"
              :key="order.id"
              class="grid gap-3 rounded-[18px] border border-accent/10 bg-bg-main/45 p-4 transition hover:border-accent/35 hover:bg-accent/5 lg:grid-cols-[minmax(0,1fr)_160px_150px_auto] lg:items-center"
            >
              <div class="min-w-0">
                <div class="mb-2 flex flex-wrap items-center gap-2">
                  <MethodBadge :method="order.payment_method" />
                  <StatusBadge :status="order.status" :expires-at="order.date_end" />
                </div>
                <UuidDisplay :value="order.uuid" />
              </div>
              <div>
                <p class="text-[11px] font-black uppercase tracking-[0.14em] text-text-muted">Сумма</p>
                <p class="mt-1 text-lg font-black text-text-main">
                  {{ formatAmount(order.amount) }} <span class="text-sm text-text-muted">{{ order.currency }}</span>
                </p>
              </div>
              <div>
                <p class="text-[11px] font-black uppercase tracking-[0.14em] text-text-muted">Создано</p>
                <p class="mt-1 text-sm font-semibold text-text-secondary">{{ formatDate(order.created_at) }}</p>
              </div>
              <BaseButton
                v-if="['pending', 'receipt_uploaded'].includes(order.status)"
                action="accept"
                variant="success"
                size="sm"
                @click="confirmOrder(order)"
              >
                Оплачено
              </BaseButton>
            </div>
          </div>
          <div v-else class="rounded-[22px] border border-dashed border-accent/20 bg-bg-main/35 p-8 text-center">
            <p class="text-lg font-black text-text-main">Активных сделок нет</p>
            <p class="mt-2 text-sm text-text-muted">Очередь свободна, можно принимать новый поток.</p>
          </div>
        </div>

        <div class="rounded-[26px] border border-border bg-bg-surface/70 p-5 shadow-[0_24px_80px_rgba(0,0,0,0.28)] backdrop-blur-xl">
          <div class="mb-5">
            <p class="text-xs font-black uppercase tracking-[0.16em] text-accent">Методы</p>
            <h2 class="mt-1 text-2xl font-black text-text-main">Матрица лимитов</h2>
          </div>
          <div v-if="methodRows.length" class="space-y-3">
            <div
              v-for="row in methodRows"
              :key="row.method"
              class="rounded-[18px] border border-accent/10 bg-bg-main/45 p-4"
            >
              <div class="mb-4 flex items-center justify-between gap-3">
                <MethodBadge :method="row.method" />
                <span
                  class="rounded-full border px-3 py-1 text-xs font-black"
                  :class="row.is_active ? 'border-status-success/25 bg-status-success/10 text-status-success' : 'border-border bg-bg-hover text-text-muted'"
                >
                  {{ row.is_active ? 'Активен' : 'Пауза' }}
                </span>
              </div>
              <div class="grid grid-cols-3 gap-2 text-sm">
                <div>
                  <p class="text-[11px] font-black uppercase text-text-muted">Комиссия</p>
                  <p class="mt-1 font-black text-text-main">{{ row.fee }}%</p>
                </div>
                <div>
                  <p class="text-[11px] font-black uppercase text-text-muted">Мин.</p>
                  <p class="mt-1 font-black text-text-main">{{ formatAmount(row.min_amount) }}</p>
                </div>
                <div>
                  <p class="text-[11px] font-black uppercase text-text-muted">Макс.</p>
                  <p class="mt-1 font-black text-text-main">{{ formatAmount(row.max_amount) }}</p>
                </div>
              </div>
            </div>
          </div>
          <p v-else class="rounded-[22px] border border-dashed border-accent/20 bg-bg-main/35 p-6 text-sm text-text-muted">
            Методы не настроены. Обратитесь к администратору.
          </p>
        </div>
      </section>

      <section
        v-if="achievements && achievements.enabled && (achievements.tiers.length || achievements.streaks.length)"
        class="rounded-[26px] border border-border bg-bg-surface/70 p-5 shadow-[0_24px_80px_rgba(0,0,0,0.28)] backdrop-blur-xl"
      >
        <div class="mb-5 flex items-center justify-between gap-3">
          <div>
            <p class="text-xs font-black uppercase tracking-[0.16em] text-accent">Рейтинг</p>
            <h2 class="mt-1 text-2xl font-black text-text-main">Достижения и бонусы</h2>
          </div>
          <div class="hidden rounded-full border border-accent/15 bg-bg-main/45 px-3 py-1.5 text-sm font-black text-accent sm:block">
            +{{ achievements.total_bonus_percent }}%
          </div>
        </div>
        <div class="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <TierProgressBar v-for="(t, i) in achievements.tiers" :key="'tier-' + i" :tier="t" />
          <StreakRing v-for="(s, i) in achievements.streaks" :key="'streak-' + i" :streak="s" />
        </div>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, defineComponent, h, onMounted, ref } from 'vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import TierProgressBar from '@/components/achievements/TierProgressBar.vue'
import StreakRing from '@/components/achievements/StreakRing.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { tradersService } from '@/api/services/traders.service'
import { ordersService } from '@/api/services/orders.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useActiveStats } from '@/composables/useActiveStats'
import type { PaymentMethod, Trader, Order, TraderAchievements } from '@/types'
import { Activity, Gauge } from 'lucide-vue-next'
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

const TerminalToggle = defineComponent({
  props: {
    label: { type: String, required: true },
    active: { type: Boolean, required: true },
    loading: { type: Boolean, default: false },
  },
  emits: ['toggle'],
  setup(props, { emit }) {
    return () => h('button', {
      type: 'button',
      disabled: props.loading,
      class: [
        'rounded-[18px] border p-4 text-left transition disabled:opacity-60',
        props.active
          ? 'border-status-success/25 bg-status-success/10'
          : 'border-border bg-bg-main/50 hover:border-accent/30',
      ],
      onClick: () => emit('toggle'),
    }, [
      h('span', { class: 'block text-[11px] font-black uppercase tracking-[0.14em] text-text-muted' }, props.label),
      h('span', {
        class: [
          'mt-3 inline-flex items-center gap-2 text-sm font-black',
          props.active ? 'text-status-success' : 'text-text-secondary',
        ],
      }, [
        h('span', { class: ['h-2 w-2 rounded-full', props.active ? 'bg-status-success' : 'bg-text-muted'] }),
        props.active ? 'Активен' : 'Пауза',
      ]),
    ])
  },
})

const traderStatusLabel = computed(() => {
  const map: Record<string, string> = { enabled: 'Включён', disabled: 'Выключен', blocked: 'Заблокирован' }
  return trader.value ? map[trader.value.status] ?? trader.value.status : '—'
})

const methodRows = computed(() =>
  Object.entries(trader.value?.methods_config || {}).map(([method, cfg]) => ({
    method: method as PaymentMethod,
    ...cfg,
  })),
)

const activeOrderPreview = computed(() => activeOrders.value.slice(0, 5))

const activeVolumeUsdt = computed(() =>
  activeOrders.value.reduce((sum, order) => sum + Number(order.amount_usdt || 0), 0),
)

const terminalMetrics = computed(() => [
  {
    label: 'Активные сделки',
    value: activeOrders.value.length,
    caption: activeOrders.value.length ? 'В рабочей очереди' : 'Очередь свободна',
  },
  {
    label: 'Объем в работе',
    value: activeVolumeUsdt.value ? `${formatAmount(activeVolumeUsdt.value)} USDT` : '—',
    caption: 'По активным ордерам',
  },
  {
    label: 'Бонус',
    value: achievements.value?.enabled ? `+${achievements.value.total_bonus_percent}%` : '—',
    caption: `${methodRows.value.length} методов настроено`,
  },
])

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

<style scoped>
.terminal-grid {
  background-image:
    linear-gradient(rgba(214, 163, 143, 0.14) 1px, transparent 1px),
    linear-gradient(90deg, rgba(214, 163, 143, 0.11) 1px, transparent 1px);
  background-size: 42px 42px;
}

.payment-flow {
  position: absolute;
  height: 1px;
  transform-origin: left center;
  background: linear-gradient(90deg, transparent, rgba(214, 163, 143, 0.9), rgba(139, 21, 56, 0.75), transparent);
  box-shadow: 0 0 22px rgba(139, 21, 56, 0.45);
}

.flow-a {
  left: 15%;
  top: 21%;
  width: 68%;
  transform: rotate(8deg);
}

.flow-b {
  left: 33%;
  top: 61%;
  width: 52%;
  transform: rotate(-24deg);
}

.flow-c {
  left: 14%;
  top: 23%;
  width: 38%;
  transform: rotate(62deg);
}
</style>
