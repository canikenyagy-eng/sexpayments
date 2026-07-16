<template>
  <div>
    <PageHeader title="Сделки" />

    <div class="mb-4 flex flex-wrap items-center gap-2">
      <BaseInput
        v-model="filters.id_search"
        placeholder="Поиск по UUID / external ID / ID / реквизиту"
        class="min-w-[280px] flex-1"
        @keyup.enter="page = 1; load()"
      />
      <BaseFilter :active-count="activeFilterCount" @apply="page = 1; load()" @reset="resetFilters">
        <BaseSelect v-model="filters.status" label="Статус" :options="statusOptions" />
        <BaseSelect v-model="filters.payment_method" label="Метод" :options="methodOptions" />
        <div class="grid grid-cols-2 gap-2">
          <BaseInput v-model="filters.amount_from" label="Сумма от" type="number" placeholder="0" />
          <BaseInput v-model="filters.amount_to" label="Сумма до" type="number" placeholder="∞" />
        </div>
      </BaseFilter>
    </div>

    <DataTable
      :columns="columns"
      :rows="orders"
      :loading="loading"
      row-key="id"
      clickable
      :current-page="page"
      :total-pages="totalPages"
      :per-page="perPage"
      @page-change="p => { page = p; load() }"
      @per-page-change="(n: number) => { perPage = n; page = 1; load() }"
      @row-click="onRowClick"
    >
      <template #cell-uuid="{ value }">
        <UuidDisplay :value="value" />
      </template>
      <template #cell-amount="{ row }">
        <span class="font-bold text-text-main">{{ formatAmount((row as any).amount) }}</span>
        <span class="ml-1 text-text-muted">{{ (row as any).currency }}</span>
      </template>
      <template #cell-exchange_rate="{ value }">
        <span class="text-text-main">{{ value != null ? formatAmount(Number(value)) : '—' }}</span>
      </template>
      <template #cell-amount_usdt="{ value }">
        <span class="text-text-main">{{ value != null ? formatAmount(Number(value)) : '—' }}</span>
      </template>
      <template #cell-payment_method="{ value }">
        <MethodBadge :method="value" />
      </template>
      <template #cell-requisite_account="{ row }">
        <div v-if="(row as Order).requisite" class="flex items-center gap-1">
          <PaymentOptionLogo
            :src="(row as Order).requisite!.logo_url ?? null"
            :alt="(row as Order).requisite!.bank_name"
            :size="16"
          />
          <button
            class="group flex items-center rounded px-1 py-0.5 transition-colors hover:bg-bg-hover active:bg-bg-card"
            title="Скопировать"
            @click.stop="copyRequisite((row as Order).requisite!.account_number)"
          >
            <span class="font-mono text-xs text-text-main transition-colors group-hover:text-accent">
              {{ (row as Order).requisite!.account_number }}
            </span>
          </button>
        </div>
        <span v-else class="text-text-muted">—</span>
      </template>
      <template #cell-trader_fee_usdt="{ value }">
        <span class="font-mono text-text-main">
          {{ value != null ? formatAmount(Number(value)) : '—' }}
        </span>
      </template>
      <template #cell-status="{ row }">
        <StatusBadge
          :status="(row as Order).status"
          :expires-at="(row as Order).date_end"
        />
      </template>
      <template #cell-created_at="{ value }">
        {{ formatDate(value) }}
      </template>
      <template #actions="{ row }">
        <BaseButton
          v-if="(row as any).receipt_file"
          variant="dark"
          size="sm"
          title="Открыть чек"
          @click.stop="openReceipt((row as any).uuid)"
        >
          Чек
        </BaseButton>
        <button
          v-if="(row as any).receipt_file"
          type="button"
          class="inline-flex h-8 w-8 items-center justify-center rounded-lg transition-colors hover:bg-bg-hover"
          :class="shieldBtnCls((row as any).uuid)"
          :title="shieldBtnTitle((row as any).uuid)"
          @click.stop="openCheckModal((row as any).uuid)"
        >
          <component :is="shieldBtnIcon((row as any).uuid)" class="h-4 w-4" />
        </button>
        <BaseButton
          v-if="['pending', 'receipt_uploaded'].includes((row as any).status)"
          action="accept"
          variant="success"
          size="sm"
          :loading="confirming === (row as any).id"
          @click.stop="markPaid(row as Order)"
        >
          Оплачено
        </BaseButton>
      </template>
    </DataTable>

    <TraderOrderDetailModal
      v-model="showOrderInfo"
      :order="selectedOrder"
      :check="selectedOrderCheck"
      :settling="selectedOrder != null && confirming === selectedOrder.id"
      @check-receipt="openCheckModal"
      @settle-failed="settleFailed"
    />

    <ReceiptCheckModal
      v-if="modalOrderUuid"
      v-model="showCheckModal"
      :order-uuid="modalOrderUuid"
      :existing-check="modalExistingCheck"
      @completed="onCheckCompleted"
    />

    <!-- Receipt picker — shown when an order has more than one receipt. -->
    <BaseModal v-model="showReceiptPicker" title="Выберите чек" size="sm">
      <ul class="divide-y divide-border">
        <li
          v-for="(r, i) in receiptPickerList"
          :key="r.uuid"
          class="flex items-center justify-between gap-3 py-2"
        >
          <div class="min-w-0">
            <p class="text-sm text-text-main">Чек {{ i + 1 }}</p>
            <p class="text-xs text-text-muted">
              {{ formatDate(r.created_at) }} · {{ receiptSourceLabels[r.source] || r.source }}
            </p>
          </div>
          <BaseButton variant="dark" size="sm" @click="openSingleReceipt(r.uuid)">
            Открыть
          </BaseButton>
        </li>
      </ul>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import BaseFilter from '@/components/ui/BaseFilter.vue'
import PaymentOptionLogo from '@/components/ui/PaymentOptionLogo.vue'
import TraderOrderDetailModal from '@/components/modals/TraderOrderDetailModal.vue'
import { ordersService, type ReceiptItem } from '@/api/services/orders.service'
import { receiptChecksService } from '@/api/services/receiptChecks.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { formatAmount, formatDate, copyToClipboard } from '@/utils/format'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import { downloadReceipt, preOpenReceiptTab } from '@/utils/receipt'
import { orderStatusOptionsWithAll, paymentMethodOptionsWithAll } from '@/constants'
import { Shield, ShieldAlert, ShieldCheck, ShieldOff } from 'lucide-vue-next'
import ReceiptCheckModal from '@/components/receipt-check/ReceiptCheckModal.vue'
import type { Order, OrderStatus, PaymentMethod, ReceiptCheck } from '@/types'

const toast = useToast()
const { confirm: askConfirm } = useConfirm()

const loading = ref(true)
const orders = ref<Order[]>([])
const confirming = ref<number | null>(null)
const page = ref(1)
const perPage = ref(25)
const totalItems = ref(0)
const totalPages = computed(() => Math.max(1, Math.ceil(totalItems.value / perPage.value)))

const showOrderInfo = ref(false)
const selectedOrder = ref<Order | null>(null)
const selectedOrderCheck = ref<ReceiptCheck | null>(null)

// Receipt picker — shown when an order has more than one receipt.
const showReceiptPicker = ref(false)
const receiptPickerList = ref<ReceiptItem[]>([])
const receiptPickerOrderUuid = ref<string | null>(null)
const receiptSourceLabels: Record<string, string> = {
  merchant_api: 'API',
  merchant_web: 'Кабинет',
  dispute_bot: 'Бот аппеляций',
  system: 'Система',
  trader: 'Трейдер',
}

// uuid → latest ReceiptCheck, populated lazily so the shield icon in the
// list can show colour-coded state without spamming the backend on every
// page render.
const checksByUuid = ref<Record<string, ReceiptCheck | null>>({})
const showCheckModal = ref(false)
const modalOrderUuid = ref<string | null>(null)
const modalExistingCheck = ref<ReceiptCheck | null>(null)

interface Filters {
  id_search: string
  status: OrderStatus | ''
  payment_method: PaymentMethod | ''
  amount_from: string
  amount_to: string
}

const filters = reactive<Filters>({
  id_search: '',
  status: '',
  payment_method: '',
  amount_from: '',
  amount_to: '',
})

const columns: Column[] = [
  { key: 'uuid', label: 'UUID' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'exchange_rate', label: 'Курс', align: 'right' },
  { key: 'amount_usdt', label: 'USDT', align: 'right' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'requisite_account', label: 'Реквизит' },
  { key: 'trader_fee_usdt', label: 'Прибыль', align: 'right' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Создан' },
]

const statusOptions = orderStatusOptionsWithAll
const methodOptions = paymentMethodOptionsWithAll

const activeFilterCount = computed(() => {
  let n = 0
  if (filters.status) n++
  if (filters.payment_method) n++
  if (filters.amount_from) n++
  if (filters.amount_to) n++
  return n
})

function onRowClick(row: Record<string, any>) {
  openOrderInfo(row as Order)
}

function resetFilters() {
  page.value = 1
  filters.id_search = ''
  filters.status = ''
  filters.payment_method = ''
  filters.amount_from = ''
  filters.amount_to = ''
  load()
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = { skip: (page.value - 1) * perPage.value, limit: perPage.value }
    if (filters.status) params.status = filters.status
    if (filters.payment_method) params.payment_method = filters.payment_method
    if (filters.id_search.trim()) params.id_search = filters.id_search.trim()
    if (filters.amount_from) params.amount_from = Number(filters.amount_from)
    if (filters.amount_to) params.amount_to = Number(filters.amount_to)
    const { data } = await ordersService.listMy(params)
    orders.value = data.items
    totalItems.value = data.total
    void prefetchChecks(data.items)
  } catch { toast.error('Ошибка загрузки') }
  finally { loading.value = false }
}

async function prefetchChecks(rows: Order[]) {
  const targets = rows.filter(
    (r) => (r as any).receipt_file && !(r.uuid in checksByUuid.value),
  )
  if (targets.length === 0) return

  try {
    const { data } = await receiptChecksService.getLatestBulk(targets.map((r) => r.uuid))
    checksByUuid.value = { ...checksByUuid.value, ...data }
  } catch {
    // Failure is non-fatal — shields just stay neutral until next refresh.
    const blanks = Object.fromEntries(targets.map((r) => [r.uuid, null]))
    checksByUuid.value = { ...checksByUuid.value, ...blanks }
  }
}

function shieldVariant(uuid: string): 'idle' | 'clean' | 'dirty' | 'failed' | 'pending' {
  const c = checksByUuid.value[uuid]
  if (!c) return 'idle'
  if (c.status === 'pending') return 'pending'
  if (c.status === 'failed') return 'failed'
  if (c.is_clean === true) return 'clean'
  if (c.is_clean === false) return 'dirty'
  return 'idle'
}

function shieldBtnIcon(uuid: string) {
  switch (shieldVariant(uuid)) {
    case 'clean': return ShieldCheck
    case 'dirty': return ShieldAlert
    case 'failed': return ShieldOff
    default: return Shield
  }
}

function shieldBtnCls(uuid: string): string {
  switch (shieldVariant(uuid)) {
    case 'clean': return 'text-status-success'
    case 'dirty': return 'text-status-danger'
    case 'failed': return 'text-status-warning'
    case 'pending': return 'text-status-warning animate-pulse'
    default: return 'text-text-muted'
  }
}

function shieldBtnTitle(uuid: string): string {
  switch (shieldVariant(uuid)) {
    case 'clean': return 'Чек проверен — чист'
    case 'dirty': return 'Чек проверен — есть подозрения'
    case 'failed': return 'Ошибка прошлой проверки — нажмите для повтора'
    case 'pending': return 'Проверка ещё выполняется'
    default: return 'Проверить чек'
  }
}

function openCheckModal(uuid: string) {
  modalOrderUuid.value = uuid
  modalExistingCheck.value = checksByUuid.value[uuid] ?? null
  showCheckModal.value = true
}

function onCheckCompleted(check: ReceiptCheck) {
  checksByUuid.value = { ...checksByUuid.value, [modalOrderUuid.value!]: check }
  if (selectedOrder.value && selectedOrder.value.uuid === modalOrderUuid.value) {
    selectedOrderCheck.value = check
  }
}

async function markPaid(order: Order) {
  const ok = await askConfirm({
    title: 'Подтвердить оплату',
    message: `Подтвердить получение оплаты по ордеру на ${formatAmount(order.amount)} ${order.currency}?`,
    confirmText: 'Оплачено',
    cancelText: 'Отмена',
    variant: 'success',
  })
  if (!ok) return
  confirming.value = order.id
  try {
    await ordersService.confirmSuccess(order.id)
    toast.success('Ордер подтверждён')
    load()
  } catch { toast.error('Ошибка подтверждения') }
  finally { confirming.value = null }
}

// Settle an OLD failed/canceled order to success — a late payment arrived. The
// trader's collateral is re-frozen and settled to the merchant (no dispute).
async function settleFailed(order: Order) {
  const ok = await askConfirm({
    title: 'Провести как успешный',
    message: `Оплата по неуспешному ордеру на ${formatAmount(order.amount)} ${order.currency} всё-таки пришла? `
      + 'Залог будет списан в пользу мерчанта, а ордер станет успешным.',
    confirmText: 'Провести',
    cancelText: 'Отмена',
    variant: 'success',
  })
  if (!ok) return
  confirming.value = order.id
  try {
    await ordersService.settleFailed(order.id)
    toast.success('Ордер проведён как успешный')
    showOrderInfo.value = false
    load()
  } catch (e: any) { toast.error(e?.message || 'Не удалось провести ордер') }
  finally { confirming.value = null }
}

async function openReceipt(uuid: string) {
  // iOS Safari (and most mobile browsers) blocks window.open() once an
  // ``await`` boundary breaks the user-gesture chain. Open the tab
  // synchronously here on the click, then either navigate it to the single
  // receipt, or — when there are several — close it and show a picker.
  const tab = preOpenReceiptTab()
  try {
    const { data } = await ordersService.listReceipts(uuid)
    if (data.length > 1) {
      // Multiple receipts → let the trader choose. Each pick is a fresh
      // user gesture, so it can pre-open its own tab.
      try { tab?.close() } catch { /* ignore */ }
      receiptPickerOrderUuid.value = uuid
      receiptPickerList.value = data
      showReceiptPicker.value = true
      return
    }
    // 0–1 receipts → keep the legacy single-file behaviour (latest receipt).
    await downloadReceipt(`/api/v1/orders/${uuid}/receipt`, tab)
  } catch (e: any) {
    try { tab?.close() } catch { /* ignore */ }
    toast.error(e?.message || 'Не удалось открыть чек')
  }
}

function openSingleReceipt(receiptUuid: string) {
  const orderUuid = receiptPickerOrderUuid.value
  if (!orderUuid) return
  const tab = preOpenReceiptTab()
  downloadReceipt(`/api/v1/orders/${orderUuid}/receipts/${receiptUuid}`, tab).catch((e: any) => {
    try { tab?.close() } catch { /* ignore */ }
    toast.error(e?.message || 'Не удалось открыть чек')
  })
}

async function openOrderInfo(order: Order) {
  selectedOrder.value = order
  showOrderInfo.value = true
  selectedOrderCheck.value = null
  if (!order.receipt_file) return
  try {
    const { data } = await receiptChecksService.getLatest(order.uuid)
    selectedOrderCheck.value = data ?? null
    if (data) checksByUuid.value = { ...checksByUuid.value, [order.uuid]: data }
  } catch {
    // non-fatal: order modal still renders without the verdict card
  }
}

async function copyRequisite(text: string) {
  try {
    await copyToClipboard(text)
    toast.success('Скопировано')
  } catch {
    toast.error('Не удалось скопировать')
  }
}

onMounted(load)
</script>
