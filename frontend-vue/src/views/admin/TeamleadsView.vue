<template>
  <div>
    <PageHeader title="Тимлиды" />

    <div class="mb-4 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
      <BaseInput v-model="filters.search" label="Поиск" placeholder="логин" />
      <BaseSelect v-model="filters.is_active" label="Статус" :options="statusOptions" />
      <div class="grid grid-cols-2 gap-2">
        <BaseInput v-model="filters.balance_from" label="Баланс от (USDT)" type="number" placeholder="0" />
        <BaseInput v-model="filters.balance_to" label="Баланс до (USDT)" type="number" placeholder="∞" />
      </div>
      <div class="flex items-end gap-2 lg:col-span-4">
        <BaseButton variant="gold" size="sm" @click="applyFilters">Применить</BaseButton>
        <BaseButton variant="dark" size="sm" @click="resetFilters">Сброс</BaseButton>
      </div>
    </div>

    <DataTable
      :columns="columns"
      :rows="teamleads"
      row-key="id"
      clickable
      :loading="loading"
      :current-page="page"
      :total-pages="totalPages"
      :per-page="perPage"
      @row-click="openProfile"
      @page-change="p => { page = p; load() }"
      @per-page-change="n => { perPage = n; page = 1; load() }"
    >
      <template #cell-is_blocked="{ row }">
        <StatusBadge :status="row.is_blocked ? 'blocked' : 'active'" />
      </template>
      <template #cell-balance_usdt="{ value }">
        <span class="font-mono text-xs">{{ value != null ? Number(value).toFixed(2) : '—' }} USDT</span>
      </template>
      <template #cell-created_at="{ value }">
        {{ value ? new Date(value).toLocaleDateString('ru-RU') : '—' }}
      </template>
    </DataTable>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { teamleadsService } from '@/api/services/teamleads.service'
import { useToast } from '@/composables/useToast'
import type { TeamleadAdminItem } from '@/types'

const toast = useToast()
const router = useRouter()
const loading = ref(false)
const teamleads = ref<TeamleadAdminItem[]>([])
const page = ref(1)
const perPage = ref(25)
const totalPages = ref(1)

const filters = reactive({
  search: '',
  is_active: '' as '' | 'true' | 'false',
  balance_from: '',
  balance_to: '',
})

const columns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'username', label: 'Логин' },
  { key: 'is_blocked', label: 'Статус' },
  { key: 'balance_usdt', label: 'Баланс' },
  { key: 'created_at', label: 'Создан' },
]

const statusOptions = [
  { value: '', label: 'Все' },
  { value: 'true', label: 'Активен' },
  { value: 'false', label: 'Заблокирован' },
]

function applyFilters() {
  page.value = 1
  load()
}

function resetFilters() {
  filters.search = ''
  filters.is_active = ''
  filters.balance_from = ''
  filters.balance_to = ''
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const params: any = { skip: (page.value - 1) * perPage.value, limit: perPage.value }
    if (filters.search) params.search = filters.search
    if (filters.is_active) params.is_active = filters.is_active === 'true'
    if (filters.balance_from) params.balance_from = Number(filters.balance_from)
    if (filters.balance_to) params.balance_to = Number(filters.balance_to)

    const { data } = await teamleadsService.listAdmin(params)
    teamleads.value = data
    totalPages.value = data.length < perPage.value ? page.value : page.value + 1
  } catch {
    toast.error('Ошибка загрузки тимлидов')
  } finally {
    loading.value = false
  }
}

function openProfile(row: any) {
  router.push(`/admin/teamleads/${row.id}`)
}

onMounted(load)
</script>
