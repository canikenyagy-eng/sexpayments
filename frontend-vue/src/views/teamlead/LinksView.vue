<template>
  <div>
    <PageHeader title="Связи" />

    <DataTable
      :columns="columns"
      :rows="links"
      :loading="loading"
      row-key="id"
      empty-text="Нет связанных пользователей"
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
import { ref, onMounted } from 'vue'
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
