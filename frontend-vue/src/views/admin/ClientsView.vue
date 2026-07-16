<template>
  <div>
    <PageHeader title="Клиенты" />

    <div class="mb-4 flex flex-wrap items-center gap-2">
      <BaseInput
        v-model="filters.search"
        placeholder="Поиск по clientID"
        class="min-w-[280px] flex-1"
        @keyup.enter="page = 1; load()"
      />
      <BaseFilter :active-count="activeFilterCount" @apply="page = 1; load()" @reset="resetFilters">
        <BaseSelect v-model="filters.status" label="Статус" :options="statusOptions" />
        <BaseInput v-model="filters.merchant_id" label="ID мерчанта" type="number" placeholder="любой" />
      </BaseFilter>
      <BaseSort v-model="sort" :options="sortOptions" @change="page = 1; load()" />
    </div>

    <DataTable
      :columns="columns"
      :rows="clients"
      :loading="loading"
      row-key="id"
      :current-page="page"
      :total-pages="totalPages"
      @page-change="p => { page = p; load() }"
    >
      <template #cell-client_user_id="{ row }">
        <UuidDisplay :value="(row as any).client_user_id" variant="full" success-message="ClientID скопирован" />
      </template>
      <template #cell-merchant_name="{ row }">
        <span class="text-text-main">{{ (row as any).merchant_name ?? `#${(row as any).merchant_id}` }}</span>
      </template>
      <template #cell-total_orders="{ value }">{{ formatInt(Number(value)) }}</template>
      <template #cell-successful_orders="{ value }">{{ formatInt(Number(value)) }}</template>
      <template #cell-turnover_usdt="{ value }">
        <span class="text-text-main">{{ formatInt(Number(value)) }}</span>
      </template>
      <template #cell-conversion="{ value }">{{ formatPercent(Number(value)) }}</template>
      <template #cell-blocked_attempts="{ value }">
        <span :class="Number(value) > 0 ? 'font-bold text-status-danger' : 'text-text-muted'">
          {{ formatInt(Number(value)) }}
        </span>
      </template>
      <template #cell-is_blocked="{ value }">
        <BaseBadge :color="value ? 'danger' : 'success'">{{ value ? 'Заблокирован' : 'Активен' }}</BaseBadge>
      </template>
      <template #cell-last_seen_at="{ value }">
        {{ value ? formatDate(value) : '—' }}
      </template>
      <template #actions="{ row }">
        <BaseButton
          v-if="(row as any).is_blocked"
          variant="dark"
          size="sm"
          :loading="actingId === (row as any).id"
          @click="unblock(row as Client)"
        >
          Разблокировать
        </BaseButton>
        <BaseButton
          v-else
          variant="danger"
          size="sm"
          :loading="actingId === (row as any).id"
          @click="block(row as Client)"
        >
          Заблокировать
        </BaseButton>
      </template>
    </DataTable>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseFilter from '@/components/ui/BaseFilter.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseSort from '@/components/ui/BaseSort.vue'
import type { SortValue, SortOption } from '@/components/ui/BaseSort.vue'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import { clientsService } from '@/api/services/clients.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { formatDate, formatInt, formatPercent } from '@/utils/format'
import type { Client } from '@/types'

const toast = useToast()
const { confirm } = useConfirm()
const PER_PAGE = 25

const loading = ref(true)
const clients = ref<Client[]>([])
const page = ref(1)
const totalPages = ref(1)
const actingId = ref<number | null>(null)

const filters = reactive<{ search: string; status: '' | 'blocked' | 'active'; merchant_id: string }>({
  search: '',
  status: '',
  merchant_id: '',
})

const statusOptions = [
  { value: '', label: 'Все' },
  { value: 'blocked', label: 'Заблокированы' },
  { value: 'active', label: 'Активны' },
]

const columns: Column[] = [
  { key: 'client_user_id', label: 'ClientID' },
  { key: 'merchant_name', label: 'Мерчант' },
  { key: 'total_orders', label: 'Сделок', align: 'right' },
  { key: 'successful_orders', label: 'Успешных', align: 'right' },
  { key: 'turnover_usdt', label: 'Оборот USDT', align: 'right' },
  { key: 'conversion', label: 'Конверсия', align: 'right' },
  { key: 'blocked_attempts', label: 'Блокировок', align: 'right' },
  { key: 'is_blocked', label: 'Статус' },
  { key: 'last_seen_at', label: 'Последняя активность' },
]

const sort = ref<SortValue>({ key: '', order: 'desc' })
const sortOptions: SortOption[] = [
  { key: 'last_seen_at', label: 'Последняя активность' },
  { key: 'total_orders', label: 'Сделок' },
  { key: 'successful_orders', label: 'Успешных' },
  { key: 'turnover_usdt', label: 'Оборот' },
  { key: 'conversion', label: 'Конверсия' },
  { key: 'blocked_attempts', label: 'Заблокированных попыток' },
]

const activeFilterCount = computed(() => {
  let n = 0
  if (filters.status) n++
  if (filters.merchant_id) n++
  return n
})

function resetFilters() {
  filters.search = ''
  filters.status = ''
  filters.merchant_id = ''
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = { skip: (page.value - 1) * PER_PAGE, limit: PER_PAGE }
    if (filters.search.trim()) params.search = filters.search.trim()
    if (filters.status) params.is_blocked = filters.status === 'blocked'
    if (filters.merchant_id) params.merchant_id = Number(filters.merchant_id)
    if (sort.value.key) { params.sort_by = sort.value.key; params.sort_order = sort.value.order }
    const { data } = await clientsService.listAll(params)
    clients.value = data
    totalPages.value = data.length < PER_PAGE ? page.value : page.value + 1
  } catch { toast.error('Ошибка загрузки клиентов') }
  finally { loading.value = false }
}

async function block(client: Client) {
  if (actingId.value) return
  const ok = await confirm({
    title: 'Заблокировать клиента?',
    message: `Клиент ${client.client_user_id} перестанет получать реквизиты — его запросы будут игнорироваться.`,
    variant: 'danger',
    confirmText: 'Заблокировать',
  })
  if (!ok) return
  actingId.value = client.id
  try {
    await clientsService.block({ merchant_id: client.merchant_id, client_user_id: client.client_user_id })
    toast.success('Клиент заблокирован')
    await load()
  } catch { toast.error('Не удалось заблокировать клиента') }
  finally { actingId.value = null }
}

async function unblock(client: Client) {
  if (actingId.value) return
  const ok = await confirm({
    title: 'Разблокировать клиента?',
    message: `Клиент ${client.client_user_id} снова сможет получать реквизиты.`,
    variant: 'gold',
    confirmText: 'Разблокировать',
  })
  if (!ok) return
  actingId.value = client.id
  try {
    await clientsService.unblock({ merchant_id: client.merchant_id, client_user_id: client.client_user_id })
    toast.success('Клиент разблокирован')
    await load()
  } catch { toast.error('Не удалось разблокировать клиента') }
  finally { actingId.value = null }
}

onMounted(load)
</script>
