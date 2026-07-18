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
      <div class="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <StatCard
          label="Общий заработок"
          :value="`${formatAmount(stats.total_earned_usdt)} USDT`"
          :icon="Wallet"
        />
        <StatCard
          label="Количество сделок"
          :value="stats.orders_count"
          :icon="Package"
        />
        <StatCard
          label="Активных связей"
          :value="stats.active_links_count"
          :icon="LinkIcon"
        />
      </div>

      <div class="grid grid-cols-1 gap-6 xl:grid-cols-[1fr_0.72fr]">
        <BaseCard title="Активные связи">
          <div v-if="topLinks.length" class="grid gap-3">
            <div
              v-for="link in topLinks"
              :key="link.id"
              class="grid gap-3 rounded-[22px] border border-accent/10 bg-bg-main/45 p-4 sm:grid-cols-[1fr_auto] sm:items-center"
            >
              <div>
                <p class="font-black text-text-main">{{ link.login }}</p>
                <p class="mt-1 text-sm text-text-muted">{{ linkTypeLabel(link.linked_entity_type) }}</p>
              </div>
              <div class="flex flex-wrap gap-2 text-xs font-black text-accent">
                <span class="rounded-full border border-accent/20 bg-accent-dark/10 px-3 py-1">
                  вход {{ link.fee_percent }}%
                </span>
                <span class="rounded-full border border-accent/20 bg-accent-dark/10 px-3 py-1">
                  выход {{ link.payout_fee_percent }}%
                </span>
              </div>
            </div>
          </div>
          <p v-else class="text-sm text-text-muted">Активные связи не найдены</p>
        </BaseCard>

        <BaseCard title="Доходный контур">
          <div class="grid gap-4">
            <div class="rounded-[22px] border border-accent/10 bg-bg-main/45 p-5">
              <span class="text-xs font-black uppercase tracking-[0.14em] text-text-muted">Средний доход на сделку</span>
              <strong class="mt-3 block text-3xl font-black text-text-main">
                {{ formatAmount(avgRewardUsdt) }} USDT
              </strong>
            </div>
            <div class="rounded-[22px] border border-accent/10 bg-bg-main/45 p-5">
              <span class="text-xs font-black uppercase tracking-[0.14em] text-text-muted">Статус портфеля</span>
              <strong class="mt-3 block text-2xl font-black text-text-main">
                {{ stats.active_links_count ? 'Активен' : 'Ожидает связей' }}
              </strong>
            </div>
          </div>
        </BaseCard>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, onMounted } from 'vue'
import AccountHero from '@/components/layout/AccountHero.vue'
import StatCard from '@/components/ui/StatCard.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import { teamleadsService } from '@/api/services/teamleads.service'
import { useToast } from '@/composables/useToast'
import { formatAmount } from '@/utils/format'
import type { TeamleadLinkEnriched } from '@/types'
import { Wallet, Package, Link as LinkIcon } from 'lucide-vue-next'

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
