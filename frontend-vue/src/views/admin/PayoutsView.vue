<template>
  <div>
    <PageHeader title="Выплаты" />

    <div class="mb-4 grid grid-cols-2 gap-3 md:grid-cols-2 lg:grid-cols-4">
      <BaseSelect v-model="filters.status" label="Статус" :options="statusOptions" />
      <BaseInput v-model="filters.terminal_id" label="ID терминала" type="number" placeholder="terminal" />
      <BaseInput v-model="filters.trader_id" label="ID трейдера" type="number" placeholder="trader" />
      <div class="col-span-2 flex items-end gap-2 md:col-span-1">
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
        <UuidDisplay :value="row.uuid" />
      </template>
      <template #cell-amount="{ row }">
        <Money :amount="row.amount" :currency="row.currency" mode="code" />
      </template>
      <template #cell-amount_usdt="{ value }">
        <Money v-if="value != null" :amount="value" currency="USDT" mode="symbol" />
        <span v-else>—</span>
      </template>
      <template #cell-payment_method="{ value }">
        <MethodBadge :method="value" />
      </template>
      <template #cell-status="{ value }">
        <StatusBadge :status="value" />
      </template>
      <template #cell-terminal_name="{ value }">
        <span class="text-xs">{{ value }}</span>
      </template>
      <template #cell-trader_login="{ value }">
        <span class="text-xs">{{ value }}</span>
      </template>
      <template #cell-created_at="{ value }">{{ formatDate(value) }}</template>
    </DataTable>

    <!-- Payout detail modal -->
    <BaseModal v-model="showInfo" title="Выплата">
      <div v-if="selected" class="space-y-4 text-sm">
        <div class="flex items-center justify-between">
          <UuidDisplay :value="selected.id" :truncate="false" show-icon />
          <StatusBadge :status="selected.status" />
        </div>
        <div class="grid grid-cols-2 gap-2">
          <div><span class="text-text-muted">External ID:</span> {{ selected.external_id }}</div>
          <div><span class="text-text-muted">Метод:</span> {{ selected.payment_method }}</div>
          <div><span class="text-text-muted">Сумма:</span> <Money :amount="selected.amount" :currency="selected.currency" mode="code" /></div>
          <div><span class="text-text-muted">USDT:</span> {{ selected.amount_usdt ?? '—' }}</div>
          <div><span class="text-text-muted">Терминал:</span> {{ terminalName(selected.payout_terminal_id) }}</div>
          <div><span class="text-text-muted">Трейдер:</span> {{ traderLogin(selected.trader_id) }}</div>
          <div><span class="text-text-muted">Комиссия USDT:</span> {{ selected.merchant_fee_usdt ?? '—' }}</div>
          <div><span class="text-text-muted">Награда USDT:</span> {{ selected.trader_fee_usdt ?? '—' }}</div>
          <div class="col-span-2"><span class="text-text-muted">Реквизит:</span> <code>{{ selected.req_number }}</code> · {{ selected.req_holder }}</div>
          <div v-if="selected.rejection_reason" class="col-span-2 text-status-danger">{{ selected.rejection_reason }}</div>
        </div>

        <div>
          <p class="mb-2 font-bold text-text-main">Чеки</p>
          <div v-if="!receipts.length" class="text-text-muted">Чеков нет.</div>
          <div v-else class="space-y-2">
            <div v-for="r in receipts" :key="r.id" class="flex items-center justify-between rounded-lg bg-bg-card p-3">
              <div>
                <div>Сумма: <b>{{ formatAmount(r.amount) }}</b> · <StatusBadge :status="r.status" /></div>
                <div v-if="r.rejection_reason" class="text-xs text-status-danger">{{ r.rejection_reason }}</div>
              </div>
              <div v-if="r.status === 'pending'" class="flex gap-2">
                <BaseButton variant="gold" size="sm" :loading="saving" @click="approve(r.id)">Принять</BaseButton>
                <BaseButton variant="dark" size="sm" :loading="saving" @click="reject(r.id)">Отклонить</BaseButton>
              </div>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showInfo = false">Закрыть</BaseButton>
        <template v-if="selected && !isTerminal(selected.status)">
          <BaseButton variant="dark" :loading="saving" @click="cancel(selected)">Отменить</BaseButton>
          <BaseButton variant="gold" :loading="saving" @click="complete(selected)">Закрыть выплату</BaseButton>
        </template>
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
import MethodBadge from '@/components/ui/MethodBadge.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import Money from '@/components/ui/Money.vue'
import { payoutsService } from '@/api/services/payouts.service'
import { usersService } from '@/api/services/users.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { formatAmount, formatDate } from '@/utils/format'
import type { AdminPayout, PayoutReceiptItem } from '@/types'

const toast = useToast()
const { confirm } = useConfirm()

const loading = ref(false)
const saving = ref(false)
const payouts = ref<AdminPayout[]>([])
const page = ref(1)
const perPage = ref(25)
const totalItems = ref(0)
const totalPages = computed(() => Math.max(1, Math.ceil(totalItems.value / perPage.value)))

const filters = reactive({ status: '', terminal_id: '', trader_id: '' })

const usersMap = ref<Record<number, string>>({})
const terminalsMap = ref<Record<number, string>>({})

const showInfo = ref(false)
const selected = ref<AdminPayout | null>(null)
const receipts = ref<PayoutReceiptItem[]>([])

const columns: Column[] = [
  { key: 'uuid', label: 'UUID' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'amount_usdt', label: 'USDT', align: 'right' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'status', label: 'Статус' },
  { key: 'terminal_name', label: 'Терминал' },
  { key: 'trader_login', label: 'Трейдер' },
  { key: 'created_at', label: 'Создан' },
]

const statusOptions = [
  { value: '', label: 'Все' },
  { value: 'created', label: 'Created' }, { value: 'claimed', label: 'Claimed' },
  { value: 'awaiting_check', label: 'Awaiting check' }, { value: 'completed', label: 'Completed' },
  { value: 'canceled', label: 'Canceled' }, { value: 'expired', label: 'Expired' },
]

function isTerminal(s: string) { return ['completed', 'canceled', 'expired'].includes(s) }
function terminalName(id?: number | null): string {
  if (id == null) return '—'
  return terminalsMap.value[id] ?? `#${id}`
}
function traderLogin(id?: number | null): string {
  if (!id) return '—'
  return usersMap.value[id] ?? `#${id}`
}

const rows = computed(() =>
  payouts.value.map(p => ({
    ...p,
    uuid: p.id,
    terminal_name: terminalName(p.payout_terminal_id),
    trader_login: traderLogin(p.trader_id),
  }))
)

function applyFilters() { page.value = 1; load() }
function resetFilters() { filters.status = ''; filters.terminal_id = ''; filters.trader_id = ''; page.value = 1; load() }
function onPerPageChange(size: number) { perPage.value = size; page.value = 1; load() }

async function load() {
  loading.value = true
  try {
    const params: any = { skip: (page.value - 1) * perPage.value, limit: perPage.value }
    if (filters.status) params.status = filters.status
    if (filters.terminal_id) params.terminal_id = Number(filters.terminal_id)
    if (filters.trader_id) params.trader_id = Number(filters.trader_id)

    const { data } = await payoutsService.listPayouts(params)
    payouts.value = data.items
    totalItems.value = data.total

    const [termRes, usersRes] = await Promise.all([
      payoutsService.listTerminals({ limit: 500 }),
      usersService.list({ limit: 500 }),
    ])
    const tMap: Record<number, string> = {}
    for (const t of termRes.data) tMap[t.id] = t.name
    terminalsMap.value = tMap
    const uMap: Record<number, string> = {}
    for (const u of usersRes.data) uMap[u.id] = u.username
    usersMap.value = uMap
  } catch { toast.error('Ошибка загрузки выплат') }
  finally { loading.value = false }
}

async function openInfo(row: AdminPayout) {
  selected.value = row
  receipts.value = []
  showInfo.value = true
  try { receipts.value = (await payoutsService.listReceipts(row.id)).data }
  catch { /* receipts optional */ }
}

async function refreshReceipts() {
  if (selected.value) receipts.value = (await payoutsService.listReceipts(selected.value.id)).data
}

async function approve(id: number) {
  saving.value = true
  try { await payoutsService.approveReceipt(id); toast.success('Чек принят'); await refreshReceipts(); load() }
  catch (e: any) { toast.error(e.response?.data?.detail || 'Ошибка') }
  finally { saving.value = false }
}

async function reject(id: number) {
  const reason = window.prompt('Причина отклонения:') || ''
  if (!reason) return
  saving.value = true
  try { await payoutsService.rejectReceipt(id, reason); toast.success('Чек отклонён'); await refreshReceipts(); load() }
  catch (e: any) { toast.error(e.response?.data?.detail || 'Ошибка') }
  finally { saving.value = false }
}

async function complete(p: AdminPayout) {
  const ok = await confirm({ title: 'Закрыть выплату', message: 'Принудительно завершить и рассчитать?', confirmText: 'Закрыть', variant: 'danger' })
  if (!ok) return
  saving.value = true
  try { await payoutsService.adminComplete(p.id); toast.success('Выплата закрыта'); showInfo.value = false; load() }
  catch (e: any) { toast.error(e.response?.data?.detail || 'Ошибка') }
  finally { saving.value = false }
}

async function cancel(p: AdminPayout) {
  const ok = await confirm({ title: 'Отменить выплату', message: 'Отменить и вернуть средства терминалу?', confirmText: 'Отменить', variant: 'danger' })
  if (!ok) return
  saving.value = true
  try { await payoutsService.adminCancel(p.id); toast.success('Выплата отменена'); showInfo.value = false; load() }
  catch (e: any) { toast.error(e.response?.data?.detail || 'Ошибка') }
  finally { saving.value = false }
}

onMounted(load)
</script>
