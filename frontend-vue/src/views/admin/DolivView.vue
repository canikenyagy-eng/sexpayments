<template>
  <div>
    <PageHeader title="Доливы" />

    <div class="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
      <BaseSelect v-model="filters.status" label="Статус" :options="statusFilterOptions" />
      <BaseInput v-model="filters.requester_trader_id" label="Заказчик (ID)" type="number" placeholder="trader" />
      <BaseInput v-model="filters.executor_trader_id" label="Доливщик (ID)" type="number" placeholder="trader" />
      <BaseInput v-model="filters.created_from" label="С даты" type="date" />
      <BaseInput v-model="filters.created_to" label="По дату" type="date" />
      <BaseInput v-model="filters.search" label="Поиск" placeholder="ID / реквизит / ордер" />
      <div class="col-span-2 flex items-end gap-2 md:col-span-3 lg:col-span-6">
        <BaseButton variant="gold" size="sm" @click="applyFilters">Применить</BaseButton>
        <BaseButton variant="dark" size="sm" @click="resetFilters">Сброс</BaseButton>
      </div>
    </div>

    <DataTable
      :columns="columns"
      :rows="rows"
      :loading="loading"
      row-key="id"
      clickable
      :current-page="page"
      :total-pages="totalPages"
      :per-page="perPage"
      @page-change="p => { page = p; load() }"
      @per-page-change="onPerPageChange"
      @row-click="openInfo($event as any)"
    >
      <template #cell-uuid="{ row }">
        <UuidDisplay :value="(row as any).id" />
      </template>
      <template #cell-requester="{ value }"><span class="text-xs">{{ value }}</span></template>
      <template #cell-executor="{ value }"><span class="text-xs">{{ value }}</span></template>
      <template #cell-amount="{ row }">
        <Money :amount="(row as any).amount" :currency="(row as any).currency" mode="code" />
      </template>
      <template #cell-amount_usdt="{ value }">
        <Money v-if="value != null" :amount="value" currency="USDT" mode="symbol" />
        <span v-else>—</span>
      </template>
      <template #cell-price_usdt="{ value }">
        <Money v-if="value != null" :amount="value" currency="USDT" mode="symbol" />
        <span v-else>—</span>
      </template>
      <template #cell-payment_method="{ value }"><MethodBadge :method="value" /></template>
      <template #cell-status="{ value }"><StatusBadge :status="value" context="doliv" /></template>
      <template #cell-created_at="{ value }">{{ formatDate(value) }}</template>
    </DataTable>

    <!-- Долив detail + status machine -->
    <BaseModal v-model="showInfo" title="Долив">
      <div v-if="selected" class="space-y-4 text-sm">
        <div class="flex items-center justify-between">
          <UuidDisplay :value="selected.id" :truncate="false" show-icon />
          <StatusBadge :status="selected.status" context="doliv" />
        </div>
        <div class="grid grid-cols-2 gap-2">
          <div><span class="text-text-muted">Заказчик:</span> {{ traderLabel(selected.requester_username, selected.requester_trader_id) }}</div>
          <div><span class="text-text-muted">Доливщик:</span> {{ traderLabel(selected.executor_username, selected.executor_trader_id) }}</div>
          <div><span class="text-text-muted">Метод:</span> {{ selected.payment_method }}</div>
          <div><span class="text-text-muted">Реквизит:</span> #{{ selected.refill_requisite_id ?? '—' }}</div>
          <div><span class="text-text-muted">Сумма:</span> <Money :amount="selected.amount" :currency="selected.currency" mode="code" /></div>
          <div><span class="text-text-muted">USDT:</span> {{ selected.amount_usdt ?? '—' }}</div>
          <div><span class="text-text-muted">Курс:</span> {{ selected.exchange_rate ?? '—' }}</div>
          <div><span class="text-text-muted">Цена USDT:</span> {{ selected.price_usdt ?? '—' }}</div>
          <div><span class="text-text-muted">Награда доливщику:</span> {{ selected.executor_reward_usdt ?? '—' }}</div>
          <div><span class="text-text-muted">Банк:</span> {{ selected.payment_option_name ?? '—' }}</div>
          <div><span class="text-text-muted">Связанный ордер:</span> {{ selected.refill_order_id ?? '—' }}</div>
          <div class="col-span-2"><span class="text-text-muted">Реквизит:</span> <code>{{ selected.req_number }}</code><span v-if="selected.req_holder"> · {{ selected.req_holder }}</span><span v-if="selected.req_extra"> · {{ selected.req_extra }}</span></div>
          <div><span class="text-text-muted">Создан:</span> {{ formatDate(selected.created_at) }}</div>
          <div v-if="selected.claimed_at"><span class="text-text-muted">Взят:</span> {{ formatDate(selected.claimed_at) }}</div>
          <div v-if="selected.claim_expires_at"><span class="text-text-muted">Истекает заявка:</span> {{ formatDate(selected.claim_expires_at) }}</div>
          <div v-if="selected.expires_at"><span class="text-text-muted">Истекает:</span> {{ formatDate(selected.expires_at) }}</div>
          <div v-if="selected.completed_at"><span class="text-text-muted">Завершён:</span> {{ formatDate(selected.completed_at) }}</div>
          <div v-if="selected.canceled_at"><span class="text-text-muted">Отменён:</span> {{ formatDate(selected.canceled_at) }}</div>
          <div v-if="selected.has_receipt" class="col-span-2">
            <span class="text-text-muted">Чек:</span>
            <button class="ml-2 font-semibold text-accent transition hover:underline" @click="openCheck">Открыть</button>
          </div>
        </div>

        <div class="rounded-lg bg-bg-card p-3">
          <p class="mb-2 font-bold text-text-main">Сменить статус</p>
          <div class="flex items-end gap-2">
            <BaseSelect v-model="targetStatus" label="Новый статус" :options="machineOptions" class="flex-1" />
            <BaseButton variant="gold" :loading="saving" :disabled="!targetStatus" @click="applyStatus">Применить</BaseButton>
          </div>
        </div>
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showInfo = false">Закрыть</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import Money from '@/components/ui/Money.vue'
import { dolivService, type AdminDolivParams } from '@/api/services/doliv.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { downloadReceipt, preOpenReceiptTab } from '@/utils/receipt'
import { formatDate } from '@/utils/format'
import type { AdminDoliv, PayoutStatus } from '@/types'

const toast = useToast()
const { confirm } = useConfirm()

const loading = ref(false)
const saving = ref(false)
const items = ref<AdminDoliv[]>([])
const page = ref(1)
const perPage = ref(25)
const totalItems = ref(0)
const totalPages = computed(() => Math.max(1, Math.ceil(totalItems.value / perPage.value)))

const filters = reactive({
  status: '', requester_trader_id: '', executor_trader_id: '',
  created_from: '', created_to: '', search: '',
})

const showInfo = ref(false)
const selected = ref<AdminDoliv | null>(null)
const targetStatus = ref<PayoutStatus | ''>('')

const STATUSES: { value: PayoutStatus; label: string }[] = [
  { value: 'created' as PayoutStatus, label: 'Создан' },
  { value: 'claimed' as PayoutStatus, label: 'Взят' },
  { value: 'awaiting_check' as PayoutStatus, label: 'Ожидает проверки' },
  { value: 'completed' as PayoutStatus, label: 'Завершён' },
  { value: 'canceled' as PayoutStatus, label: 'Отменён' },
  { value: 'expired' as PayoutStatus, label: 'Истёк' },
]

const statusFilterOptions = [{ value: '', label: 'Все' }, ...STATUSES]

// The status machine offers every status EXCEPT the current one.
const machineOptions = computed(() =>
  STATUSES.filter(s => s.value !== selected.value?.status)
)

const columns: Column[] = [
  { key: 'uuid', label: 'ID' },
  { key: 'requester', label: 'Заказчик' },
  { key: 'executor', label: 'Доливщик' },
  { key: 'refill_requisite_id', label: 'Реквизит' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'amount_usdt', label: 'USDT', align: 'right' },
  { key: 'price_usdt', label: 'Цена', align: 'right' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Создан' },
]

function traderLabel(username?: string | null, id?: number | null): string {
  if (username) return username
  return id ? `#${id}` : '—'
}

const rows = computed(() =>
  items.value.map(d => ({
    ...d,
    uuid: d.id,
    requester: traderLabel(d.requester_username, d.requester_trader_id),
    executor: traderLabel(d.executor_username, d.executor_trader_id),
  }))
)

function applyFilters() { page.value = 1; load() }
function resetFilters() {
  filters.status = ''; filters.requester_trader_id = ''; filters.executor_trader_id = ''
  filters.created_from = ''; filters.created_to = ''; filters.search = ''
  page.value = 1; load()
}
function onPerPageChange(size: number) { perPage.value = size; page.value = 1; load() }

async function load() {
  loading.value = true
  try {
    const params: AdminDolivParams = { skip: (page.value - 1) * perPage.value, limit: perPage.value }
    if (filters.status) params.status = filters.status as PayoutStatus
    if (filters.requester_trader_id) params.requester_trader_id = Number(filters.requester_trader_id)
    if (filters.executor_trader_id) params.executor_trader_id = Number(filters.executor_trader_id)
    if (filters.created_from) params.created_from = `${filters.created_from}T00:00:00`
    if (filters.created_to) params.created_to = `${filters.created_to}T23:59:59`
    if (filters.search) params.search = filters.search.trim()

    const { data } = await dolivService.all(params)
    items.value = data.items
    totalItems.value = data.total
  } catch { toast.error('Ошибка загрузки доливов') }
  finally { loading.value = false }
}

function openInfo(row: AdminDoliv) {
  selected.value = row
  targetStatus.value = ''
  showInfo.value = true
}

async function openCheck() {
  if (!selected.value) return
  const tab = preOpenReceiptTab()
  try {
    await downloadReceipt(dolivService.receiptUrl(selected.value.id), tab)
  } catch (e: any) {
    toast.error(e?.message || 'Не удалось открыть чек')
  }
}

async function applyStatus() {
  if (!selected.value || !targetStatus.value) return
  const label = STATUSES.find(s => s.value === targetStatus.value)?.label ?? targetStatus.value
  const ok = await confirm({
    title: 'Сменить статус долива',
    message: `Перевести долив в «${label}»?`,
    confirmText: 'Сменить',
    variant: 'danger',
  })
  if (!ok) return
  saving.value = true
  try {
    const { data } = await dolivService.adminChangeStatus(selected.value.id, targetStatus.value as PayoutStatus)
    toast.success('Статус изменён')
    selected.value = data
    targetStatus.value = ''
    load()
  } catch (e: any) {
    toast.error(e.response?.data?.detail || 'Не удалось сменить статус')
  } finally { saving.value = false }
}

onMounted(load)
</script>
