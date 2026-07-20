<template>
  <div>
    <AccountHero
      role="teamlead"
      eyebrow="Кабинет тимлида"
      title="Портфель партнерских связей"
      subtitle="Контролируйте активные связи, вознаграждения и общий вклад команды без лишнего операционного шума."
      :metrics="heroMetrics"
    />

    <div v-if="loading" class="py-16"><LoadingSpinner /></div>

    <template v-else>
      <section class="mb-6 grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(360px,0.72fr)]">
        <div class="relative overflow-hidden rounded-[1.35rem] border border-accent/15 bg-bg-surface/70 p-5 shadow-[0_24px_76px_rgba(0,0,0,0.3),inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl">
          <div class="pointer-events-none absolute inset-0 sp-panel-grid opacity-45" />
          <div class="relative mb-6 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <p class="sp-kicker">Operations center</p>
              <h2 class="mt-2 text-3xl font-black leading-none text-text-main">Контроль команды</h2>
            </div>
            <RouterLink
              to="/teamlead/links"
              class="inline-flex w-fit items-center justify-center rounded-xl border border-accent/30 px-4 py-2 text-sm font-black text-accent transition hover:border-accent hover:bg-accent/10"
            >
              Управлять связями
            </RouterLink>
          </div>

          <div class="relative grid gap-3 sm:grid-cols-3">
            <article
              v-for="metric in operationMetrics"
              :key="metric.label"
              class="min-h-[136px] rounded-[1rem] border border-accent/15 bg-bg-main/60 p-4 shadow-[inset_0_1px_0_rgba(245,245,245,0.035)]"
            >
              <div class="mb-5 flex items-center justify-between gap-3">
                <p class="text-[11px] font-black uppercase tracking-[0.14em] text-text-muted">{{ metric.label }}</p>
                <span class="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-accent/15 bg-accent-dark/10 text-accent">
                  <component :is="metric.icon" class="h-4 w-4" />
                </span>
              </div>
              <strong class="block break-words text-[1.65rem] font-black leading-[1.04] text-text-main">{{ metric.value }}</strong>
              <p class="mt-3 text-xs font-semibold leading-5 text-text-muted">{{ metric.caption }}</p>
            </article>
          </div>
        </div>

        <div class="rounded-[1.35rem] border border-accent/15 bg-bg-surface/70 p-5 shadow-[0_24px_76px_rgba(0,0,0,0.26),inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl">
          <div class="mb-5 flex items-center justify-between gap-3">
            <div>
              <p class="sp-kicker">Пульс портфеля</p>
              <h2 class="mt-2 text-2xl font-black leading-none text-text-main">{{ portfolioStatus }}</h2>
            </div>
            <span class="inline-flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-black" :class="stats.active_links_count ? 'border-status-success/20 bg-status-success/10 text-status-success' : 'border-status-warning/25 bg-status-warning/10 text-status-warning'">
              <span class="h-2 w-2 rounded-full bg-current" />
              {{ stats.active_links_count ? 'Активен' : 'Пауза' }}
            </span>
          </div>

          <div class="space-y-4">
            <div v-for="row in pulseRows" :key="row.label">
              <div class="mb-2 flex items-center justify-between text-xs font-black text-text-muted">
                <span>{{ row.label }}</span>
                <span>{{ row.value }}</span>
              </div>
              <div class="h-2 overflow-hidden rounded-full bg-white/10">
                <div class="h-full rounded-full bg-gradient-to-r from-accent-dark via-accent to-status-success" :style="{ width: `${row.percent}%` }" />
              </div>
            </div>
          </div>

          <div class="mt-5 grid gap-2">
            <div
              v-for="row in portfolioRows"
              :key="row.label"
              class="grid min-h-[46px] grid-cols-[1fr_auto] items-center gap-3 rounded-xl border border-accent/10 bg-bg-main/45 px-4"
            >
              <span class="text-sm font-semibold text-text-muted">{{ row.label }}</span>
              <strong class="text-right text-sm font-black text-text-main">{{ row.value }}</strong>
            </div>
          </div>
        </div>
      </section>

      <section class="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_0.72fr]">
        <BaseCard title="Активные связи">
          <div v-if="topLinks.length" class="grid gap-3">
            <div
              v-for="link in topLinks"
              :key="link.id"
              class="grid gap-3 rounded-xl border border-accent/10 bg-bg-main/45 p-4 transition hover:border-accent/30 hover:bg-accent/5 sm:grid-cols-[1fr_auto] sm:items-center"
            >
              <div class="min-w-0">
                <div class="mb-2 flex flex-wrap items-center gap-2">
                  <span class="rounded-lg border border-accent/20 bg-accent-dark/10 px-2.5 py-1 text-[11px] font-black uppercase tracking-[0.08em] text-accent">
                    {{ linkTypeLabel(link.linked_entity_type) }}
                  </span>
                  <span class="rounded-lg border border-status-success/20 bg-status-success/10 px-2.5 py-1 text-[11px] font-black text-status-success">
                    Активна
                  </span>
                </div>
                <p class="truncate font-black text-text-main">{{ link.login }}</p>
              </div>
              <div class="grid grid-cols-2 gap-2 text-xs font-black text-accent">
                <span class="rounded-lg border border-accent/20 bg-accent-dark/10 px-3 py-2">
                  вход {{ link.fee_percent }}%
                </span>
                <span class="rounded-lg border border-accent/20 bg-accent-dark/10 px-3 py-2">
                  выход {{ link.payout_fee_percent }}%
                </span>
              </div>
            </div>
          </div>
          <p v-else class="rounded-xl border border-dashed border-accent/20 bg-bg-main/35 p-6 text-sm text-text-muted">
            Активные связи не найдены
          </p>
        </BaseCard>

        <BaseCard title="Контроль выплат">
          <div class="grid gap-4">
            <div class="rounded-xl border border-accent/10 bg-bg-main/45 p-5">
              <span class="text-[11px] font-black uppercase tracking-[0.14em] text-text-muted">Средний доход на сделку</span>
              <strong class="mt-3 block text-3xl font-black text-text-main">
                {{ formatAmount(avgRewardUsdt) }} USDT
              </strong>
              <p class="mt-2 text-sm font-semibold text-text-muted">Фактическая экономика партнерского портфеля</p>
            </div>
            <RouterLink
              to="/teamlead/finances"
              class="inline-flex min-h-[48px] items-center justify-center rounded-xl border border-accent/30 px-4 text-sm font-black text-accent transition hover:border-accent hover:bg-accent/10"
            >
              Открыть финансы
            </RouterLink>
          </div>
        </BaseCard>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, onMounted } from 'vue'
import { RouterLink } from 'vue-router'
import AccountHero from '@/components/layout/AccountHero.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import { teamleadsService } from '@/api/services/teamleads.service'
import { useToast } from '@/composables/useToast'
import { formatAmount } from '@/utils/format'
import type { TeamleadLinkEnriched } from '@/types'
import { Network, Package, Wallet } from 'lucide-vue-next'

const toast = useToast()
const loading = ref(true)
const stats = reactive({
  total_earned_usdt: 0,
  orders_count: 0,
  active_links_count: 0,
})
const links = ref<TeamleadLinkEnriched[]>([])

const topLinks = computed(() => links.value.filter(link => link.is_active).slice(0, 4))
const avgRewardUsdt = computed(() =>
  stats.orders_count > 0 ? stats.total_earned_usdt / stats.orders_count : 0,
)
const activeLinkPercent = computed(() => Math.max(0, Math.min(100, stats.active_links_count * 20)))
const orderDensityPercent = computed(() => Math.max(0, Math.min(100, stats.orders_count ? Math.round(stats.orders_count / Math.max(stats.active_links_count, 1) * 8) : 0)))
const earningPercent = computed(() => Math.max(0, Math.min(100, Math.round(avgRewardUsdt.value * 4))))
const portfolioStatus = computed(() => stats.active_links_count ? 'Портфель активен' : 'Нужны связи')

const heroMetrics = computed(() => [
  {
    label: 'Заработано',
    value: `${formatAmount(stats.total_earned_usdt)} USDT`,
    caption: 'По закрытым операциям',
  },
  {
    label: 'Сделки',
    value: stats.orders_count,
    caption: 'В партнерском портфеле',
  },
  {
    label: 'Активные связи',
    value: stats.active_links_count,
    caption: topLinks.value.length ? 'Рабочий контур включен' : 'Нужна настройка',
  },
  {
    label: 'Средняя доля',
    value: `${formatAmount(avgRewardUsdt.value)} USDT`,
    caption: 'На одну сделку',
  },
])

const operationMetrics = computed(() => [
  {
    label: 'Командный доход',
    value: `${formatAmount(stats.total_earned_usdt)} USDT`,
    caption: 'Суммарная доля тимлида',
    icon: Wallet,
  },
  {
    label: 'Операции',
    value: stats.orders_count,
    caption: 'Сделки партнерского портфеля',
    icon: Package,
  },
  {
    label: 'Связи',
    value: stats.active_links_count,
    caption: topLinks.value.length ? 'Активные рабочие линии' : 'Связи не активированы',
    icon: Network,
  },
])

const pulseRows = computed(() => [
  { label: 'Активность связей', value: `${stats.active_links_count}`, percent: activeLinkPercent.value },
  { label: 'Плотность операций', value: `${stats.orders_count}`, percent: orderDensityPercent.value },
  { label: 'Средняя экономика', value: `${formatAmount(avgRewardUsdt.value)} USDT`, percent: earningPercent.value },
])

const portfolioRows = computed(() => [
  { label: 'Всего связей', value: String(links.value.length) },
  { label: 'Активных связей', value: String(stats.active_links_count) },
  { label: 'Средний доход', value: `${formatAmount(avgRewardUsdt.value)} USDT` },
  { label: 'Статус', value: portfolioStatus.value },
])

function linkTypeLabel(type: string): string {
  if (type === 'merchant') return 'Мерчант'
  if (type === 'trader') return 'Трейдер'
  return type
}

async function load() {
  try {
    const [statsRes, linksRes] = await Promise.allSettled([
      teamleadsService.getMyStats(),
      teamleadsService.getMyLinksEnriched(),
    ])
    if (statsRes.status === 'fulfilled') {
      const data = statsRes.value.data
      stats.total_earned_usdt = Number(data.total_earned_usdt) || 0
      stats.orders_count = data.orders_count
      stats.active_links_count = data.active_links_count
    }
    if (linksRes.status === 'fulfilled') {
      links.value = linksRes.value.data
    }
  } catch {
    toast.error('Ошибка загрузки статистики')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
