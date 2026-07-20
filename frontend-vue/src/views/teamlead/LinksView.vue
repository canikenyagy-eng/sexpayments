<template>
  <div>
    <PageHeader title="Связи" />

    <section class="mb-5 grid gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(360px,0.72fr)]">
      <div class="relative overflow-hidden rounded-[1.25rem] border border-accent/15 bg-bg-surface/70 p-5 shadow-[0_22px_70px_rgba(0,0,0,0.28),inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl">
        <div class="pointer-events-none absolute inset-0 sp-panel-grid opacity-35" />
        <div class="relative flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p class="sp-kicker">Партнерский контур</p>
            <h2 class="mt-2 text-2xl font-black leading-none text-text-main">Связи команды</h2>
            <p class="mt-3 max-w-2xl text-sm font-semibold leading-6 text-text-muted">
              Мерчанты, трейдеры и комиссии сведены в единый контрольный список.
            </p>
          </div>
          <div class="grid grid-cols-3 gap-2 sm:min-w-[360px]">
            <div
              v-for="item in summaryRows"
              :key="item.label"
              class="rounded-xl border border-accent/10 bg-bg-main/55 p-3"
            >
              <span class="block text-[10px] font-black uppercase tracking-[0.12em] text-text-muted">{{ item.label }}</span>
              <strong class="mt-2 block text-lg font-black text-text-main">{{ item.value }}</strong>
            </div>
          </div>
        </div>
      </div>

      <div class="rounded-[1.25rem] border border-accent/15 bg-bg-surface/70 p-5 shadow-[0_22px_70px_rgba(0,0,0,0.24),inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl">
        <p class="sp-kicker">Фильтр связей</p>
        <h2 class="mt-2 text-xl font-black leading-none text-text-main">Операционный срез</h2>
        <div class="mt-5 grid grid-cols-2 gap-2 sm:grid-cols-3">
          <button
            v-for="option in viewOptions"
            :key="option.key"
            type="button"
            class="rounded-xl border px-3 py-2 text-xs font-black uppercase tracking-[0.08em] transition"
            :class="viewMode === option.key ? 'border-accent/35 bg-accent-dark/20 text-accent' : 'border-accent/10 bg-bg-main/45 text-text-muted hover:border-accent/25 hover:text-text-main'"
            @click="viewMode = option.key"
          >
            {{ option.label }}
          </button>
        </div>
      </div>
    </section>

    <DataTable
      :columns="columns"
      :rows="visibleLinks"
      :loading="loading"
      row-key="id"
      :show-pagination="false"
      empty-text="Связей по выбранному срезу нет"
    >
      <template #cell-login="{ row }">
        <span class="font-bold text-text-main">{{ (row as any).login }}</span>
      </template>
      <template #cell-linked_entity_type="{ value }">
        <BaseBadge :color="value === 'merchant' ? 'gold' : 'info'">
          {{ value === 'merchant' ? 'Мерчант' : 'Трейдер' }}
        </BaseBadge>
      </template>
      <template #cell-fee_percent="{ value }">
        <span class="font-bold text-accent">{{ Number(value).toFixed(2) }}%</span>
      </template>
      <template #cell-payout_fee_percent="{ value }">
        <span class="font-bold text-accent">{{ Number(value ?? 0).toFixed(2) }}%</span>
      </template>
      <template #cell-income_usdt="{ value }">
        <span class="font-bold text-status-success">{{ formatAmount(value) }}</span>
        <span class="ml-1 text-xs text-text-muted">USDT</span>
      </template>
      <template #cell-is_active="{ value }">
        <BaseBadge :color="value ? 'success' : 'default'">
          {{ value ? 'Активна' : 'Отключена' }}
        </BaseBadge>
      </template>
    </DataTable>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import { teamleadsService } from '@/api/services/teamleads.service'
import { useToast } from '@/composables/useToast'
import { formatAmount } from '@/utils/format'
import type { TeamleadLinkEnriched } from '@/types'

const toast = useToast()
const loading = ref(true)
const links = ref<TeamleadLinkEnriched[]>([])
const viewMode = ref<'all' | 'active' | 'paused' | 'merchant' | 'trader'>('all')

const viewOptions = [
  { key: 'all', label: 'Все' },
  { key: 'active', label: 'Активные' },
  { key: 'paused', label: 'Пауза' },
  { key: 'merchant', label: 'Мерчанты' },
  { key: 'trader', label: 'Трейдеры' },
] as const

const activeLinks = computed(() => links.value.filter(link => link.is_active))
const merchantLinks = computed(() => links.value.filter(link => link.linked_entity_type === 'merchant'))
const traderLinks = computed(() => links.value.filter(link => link.linked_entity_type === 'trader'))
const totalIncomeUsdt = computed(() =>
  links.value.reduce((sum, link) => sum + Number(link.income_usdt ?? 0), 0),
)
const summaryRows = computed(() => [
  { label: 'Всего', value: links.value.length },
  { label: 'Активные', value: activeLinks.value.length },
  { label: 'Доход', value: `${formatAmount(totalIncomeUsdt.value)} USDT` },
])
const visibleLinks = computed(() => {
  if (viewMode.value === 'active') return activeLinks.value
  if (viewMode.value === 'paused') return links.value.filter(link => !link.is_active)
  if (viewMode.value === 'merchant') return merchantLinks.value
  if (viewMode.value === 'trader') return traderLinks.value
  return links.value
})

const columns: Column[] = [
  { key: 'login', label: 'Логин' },
  { key: 'linked_entity_type', label: 'Роль' },
  { key: 'fee_percent', label: 'Процент (ордеры)', align: 'right' },
  { key: 'payout_fee_percent', label: 'Процент (выплаты)', align: 'right' },
  { key: 'income_usdt', label: 'Доход', align: 'right' },
  { key: 'is_active', label: 'Статус' },
]

async function load() {
  loading.value = true
  try {
    const { data } = await teamleadsService.getMyLinksEnriched()
    links.value = data
  } catch {
    toast.error('Ошибка загрузки связей')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
