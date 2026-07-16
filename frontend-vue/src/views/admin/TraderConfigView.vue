<template>
  <div>
    <div class="mb-6 flex items-center justify-between">
      <div>
        <h1 class="text-2xl font-black text-text-main">Конфиг трейдеров</h1>
      </div>
      <BaseButton variant="dark" size="sm" @click="$router.push('/admin/traders')">
        Управление трейдерами
      </BaseButton>
    </div>

    <DataTable
      :columns="columns"
      :rows="rows"
      row-key="id"
      :loading="loading"
      empty-text="Нет трейдеров"
    >
      <template #cell-status="{ value }">
        <StatusBadge :status="value" />
      </template>
      <template #cell-is_payin_active="{ value }">
        <span :class="value ? 'text-status-success' : 'text-text-muted'">
          {{ value ? 'Да' : 'Нет' }}
        </span>
      </template>
      <template #cell-is_payout_active="{ value }">
        <span :class="value ? 'text-status-success' : 'text-text-muted'">
          {{ value ? 'Да' : 'Нет' }}
        </span>
      </template>
      <template #cell-sbp_fee="{ row }">
        {{ feeStr(row, 'sbp') }}
      </template>
      <template #cell-card_fee="{ row }">
        {{ feeStr(row, 'card') }}
      </template>
      <template #cell-sim_fee="{ row }">
        {{ feeStr(row, 'sim') }}
      </template>
    </DataTable>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { tradersService } from '@/api/services/traders.service'
import { useToast } from '@/composables/useToast'
import type { Trader, PaymentMethod } from '@/types'

const toast = useToast()
const loading = ref(true)
const rows = ref<Trader[]>([])

const columns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'user_id', label: 'User ID' },
  { key: 'status', label: 'Статус' },
  { key: 'is_payin_active', label: 'Payin' },
  { key: 'is_payout_active', label: 'Payout' },
  { key: 'sbp_fee', label: 'SBP %' },
  { key: 'card_fee', label: 'Card %' },
  { key: 'sim_fee', label: 'SIM %' },
]

function feeStr(row: Record<string, any>, method: PaymentMethod): string {
  const cfg = row.methods_config?.[method]
  if (!cfg) return '—'
  return `${cfg.fee}% (${cfg.min_amount}–${cfg.max_amount})`
}

onMounted(async () => {
  try {
    const { data } = await tradersService.list({ limit: 500 })
    rows.value = data
  } catch {
    toast.error('Ошибка загрузки трейдеров')
  } finally {
    loading.value = false
  }
})
</script>
