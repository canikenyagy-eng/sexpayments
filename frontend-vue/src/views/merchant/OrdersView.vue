<template>
  <div>
    <PageHeader title="Ордера">
      <template #actions>
        <BaseButton variant="dark" size="sm" @click="showExport = true">
          <Download class="mr-1 h-4 w-4" />Excel
        </BaseButton>
        <BaseButton variant="gold" size="sm" @click="showCreate = true">Создать ордер</BaseButton>
      </template>
    </PageHeader>

    <!-- Filters -->
    <div class="mb-4 flex flex-wrap items-end gap-3">
      <BaseSelect v-model="filters.merchant_id" label="Терминал" :options="terminalFilterOptions" />
      <BaseSelect v-model="filters.status" label="Статус" :options="statusOptions" />
      <BaseSelect v-model="filters.payment_method" label="Метод" :options="methodOptions" />
      <BaseInput v-model="filters.search" label="Поиск" placeholder="UUID или external_id" />
      <BaseButton variant="dark" size="sm" @click="page = 1; load()">Применить</BaseButton>
    </div>

    <DataTable
      :columns="columns"
      :rows="orders"
      :loading="loading"
      row-key="id"
      :current-page="page"
      :total-pages="totalPages"
      :per-page="perPage"
      clickable
      @page-change="p => { page = p; load() }"
      @per-page-change="(n: number) => { perPage = n; page = 1; load() }"
      @row-click="openDetails"
    >
      <template #cell-uuid="{ value }">
        <UuidDisplay :value="String(value)" show-icon />
      </template>
      <template #cell-external_id="{ value }">
        <div v-if="value" class="flex items-center gap-1">
          <span class="max-w-[140px] truncate font-mono text-xs">{{ value }}</span>
          <button
            type="button"
            class="shrink-0 rounded p-0.5 text-text-muted transition hover:text-accent"
            @click.stop="copy(String(value))"
          >
            <Copy class="h-3.5 w-3.5" />
          </button>
        </div>
        <span v-else class="text-text-muted">—</span>
      </template>
      <template #cell-merchant_name="{ row }">
        <span class="text-xs text-text-main">
          {{ row.merchant_name || `Терминал #${row.merchant_id}` }}
        </span>
      </template>
      <template #cell-amount="{ row }">
        {{ formatAmount(row.amount) }} {{ row.currency }}
      </template>
      <template #cell-amount_usdt="{ value }">
        {{ value != null ? formatAmount(value) + ' USDT' : '—' }}
      </template>
      <template #cell-payment_method="{ value }">
        <MethodBadge :method="value" />
      </template>
      <template #cell-status="{ value }">
        <StatusBadge :status="value" />
      </template>
      <template #cell-direction="{ value }">
        <BaseBadge :color="value === 'payin' ? 'success' : 'warning'">{{ value }}</BaseBadge>
      </template>
      <template #cell-created_at="{ value }">
        {{ formatDate(value) }}
      </template>
    </DataTable>

    <!-- Order details modal -->
    <MerchantOrderDetailModal
      v-model="showDetails"
      :order="detailOrder"
      :resending="resending"
      @open-receipt="openReceipt"
      @resend-callback="resendCallback"
    />

    <!-- Create order modal -->
    <BaseModal v-model="showCreate" title="Создать Payin ордер">
      <div class="space-y-4">
        <BaseInput v-model="createForm.amount" label="Сумма" type="number" required />
        <BaseSelect v-model="createForm.currency" label="Валюта" :options="createCurrencyOptions" />
        <BaseSelect v-model="createForm.payment_method" label="Метод оплаты" :options="createMethodOptions" />
        <BaseInput v-model="createForm.internalId" label="Internal ID (опционально)" />
        <BaseInput v-model="createForm.notificationUrl" label="Webhook URL (опционально)" />
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showCreate = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="creating" @click="createOrder">Создать</BaseButton>
      </template>
    </BaseModal>

    <!-- Excel export: pick a period, download .xlsx across all terminals -->
    <BaseModal v-model="showExport" title="Выгрузка в Excel">
      <div class="space-y-4">
        <p class="text-sm text-text-muted">
          Выгрузка ордеров по всем терминалам за выбранный период.
        </p>
        <div class="flex gap-3">
          <BaseInput v-model="exportForm.date_from" label="С" type="date" class="flex-1" />
          <BaseInput v-model="exportForm.date_to" label="По" type="date" class="flex-1" />
        </div>
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showExport = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="exporting" @click="exportOrders">Выгрузить</BaseButton>
      </template>
    </BaseModal>

    <!-- Created order result -->
    <BaseModal v-model="showCreatedResult" title="Ордер создан">
      <div v-if="createdOrder" class="space-y-3">
        <div class="text-sm">
          <p><span class="text-text-muted">UUID:</span> <span class="font-mono text-text-main">{{ createdOrder.id }}</span></p>
          <p><span class="text-text-muted">Статус:</span> <StatusBadge :status="createdOrder.status" /></p>
          <p><span class="text-text-muted">Payment URL:</span></p>
          <div class="mt-1 rounded-lg bg-bg-card p-2 font-mono text-xs text-accent break-all">{{ createdOrder.payment_url }}</div>
        </div>
        <BaseButton variant="dark" size="sm" @click="copy(createdOrder.payment_url)">Копировать URL</BaseButton>
      </div>
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
import BaseBadge from '@/components/ui/BaseBadge.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import { Copy, Download } from 'lucide-vue-next'
import { merchantsService } from '@/api/services/merchants.service'
import { useToast } from '@/composables/useToast'
import { formatAmount, formatDate, copyToClipboard } from '@/utils/format'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import MerchantOrderDetailModal from '@/components/modals/MerchantOrderDetailModal.vue'
import {
  createOrderCurrencyOptions,
  orderStatusOptionsWithAll,
  paymentMethodOptions,
  paymentMethodOptionsWithAll,
} from '@/constants'
import { downloadReceipt, preOpenReceiptTab } from '@/utils/receipt'
import type { Order, MerchantOrderResponse, Currency, PaymentMethod, MerchantListItem } from '@/types'

const toast = useToast()

const loading = ref(false)
const creating = ref(false)
const resending = ref(false)
const orders = ref<Order[]>([])
const page = ref(1)
const perPage = ref(25)
const totalItems = ref(0)
const totalPages = computed(() => Math.max(1, Math.ceil(totalItems.value / perPage.value)))
const filters = reactive({ status: '', payment_method: '', search: '', merchant_id: '' })
const terminals = ref<MerchantListItem[]>([])
const terminalFilterOptions = computed(() => [
  { value: '', label: 'Все терминалы' },
  ...terminals.value.map(m => ({
    value: String(m.id),
    label: m.name || `Терминал #${m.id}`,
  })),
])

const showDetails = ref(false)
const detailOrder = ref<Order | null>(null)
const showCreate = ref(false)

// Excel export — default period = current month to today (YYYY-MM-DD).
const showExport = ref(false)
const exporting = ref(false)
const _today = new Date()
function _ymd(d: Date) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
const exportForm = reactive({
  date_from: _ymd(new Date(_today.getFullYear(), _today.getMonth(), 1)),
  date_to: _ymd(_today),
})
const showCreatedResult = ref(false)
const createdOrder = ref<MerchantOrderResponse | null>(null)

const createForm = reactive({
  amount: '',
  currency: 'RUB',
  payment_method: 'sbp',
  internalId: '',
  notificationUrl: '',
})

const columns: Column[] = [
  { key: 'uuid', label: 'UUID' },
  { key: 'external_id', label: 'External ID' },
  { key: 'merchant_name', label: 'Терминал' },
  { key: 'direction', label: 'Тип' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'amount_usdt', label: 'USDT', align: 'right' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Дата' },
]

const statusOptions = orderStatusOptionsWithAll
const methodOptions = paymentMethodOptionsWithAll
const createCurrencyOptions = createOrderCurrencyOptions
const createMethodOptions = paymentMethodOptions

function copy(text: string) {
  copyToClipboard(text).then(
    () => toast.success('Скопировано'),
    () => toast.error('Не удалось скопировать'),
  )
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = { skip: (page.value - 1) * perPage.value, limit: perPage.value }
    if (filters.status) params.status = filters.status
    if (filters.payment_method) params.payment_method = filters.payment_method
    if (filters.search) params.search = filters.search
    if (filters.merchant_id) params.merchant_id = Number(filters.merchant_id)
    const { data } = await merchantsService.listMyOrders(params)
    orders.value = data.items
    totalItems.value = data.total
  } catch {
    toast.error('Ошибка загрузки ордеров')
  } finally {
    loading.value = false
  }
}

async function exportOrders() {
  if (!exportForm.date_from || !exportForm.date_to) {
    toast.error('Укажите период')
    return
  }
  if (exportForm.date_from > exportForm.date_to) {
    toast.error('«С» не может быть позже «По»')
    return
  }
  exporting.value = true
  try {
    // Inclusive period: from start of the first day to end of the last day.
    const { data } = await merchantsService.exportMyOrders({
      date_from: `${exportForm.date_from}T00:00:00`,
      date_to: `${exportForm.date_to}T23:59:59`,
    })
    const url = URL.createObjectURL(data)
    const a = document.createElement('a')
    a.href = url
    a.download = `orders_${exportForm.date_from}_${exportForm.date_to}.xlsx`
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
    showExport.value = false
  } catch {
    toast.error('Не удалось выгрузить ордера')
  } finally {
    exporting.value = false
  }
}

function openDetails(row: Record<string, any>) {
  detailOrder.value = row as Order
  showDetails.value = true
}

function openReceipt(uuid: string) {
  // Pre-open the tab synchronously so mobile browsers honor the new
  // window — `downloadReceipt` will navigate it post-await.
  const tab = preOpenReceiptTab()
  downloadReceipt(`/api/v1/merchants/me/orders/${uuid}/receipt`, tab).catch((e: any) => {
    toast.error(e?.message || 'Не удалось открыть чек')
  })
}

async function resendCallback(row: Order) {
  resending.value = true
  try {
    await merchantsService.resendCallback(String(row.uuid))
    toast.success('Callback переотправлен')
  } catch {
    toast.error('Ошибка переотправки')
  } finally {
    resending.value = false
  }
}

async function createOrder() {
  if (!createForm.amount || Number(createForm.amount) <= 0) {
    toast.error('Укажите сумму')
    return
  }
  creating.value = true
  try {
    const { data } = await merchantsService.createPayinOrder({
      amount: Number(createForm.amount),
      currency: createForm.currency as Currency,
      payment_method: createForm.payment_method as PaymentMethod,
      internalId: createForm.internalId || undefined,
      notificationUrl: createForm.notificationUrl || undefined,
    })
    createdOrder.value = data
    showCreate.value = false
    showCreatedResult.value = true
    Object.assign(createForm, { amount: '', currency: 'RUB', payment_method: 'sbp', internalId: '', notificationUrl: '' })
    toast.success('Ордер создан')
    load()
  } catch {
    toast.error('Ошибка создания ордера')
  } finally {
    creating.value = false
  }
}

async function loadTerminals() {
  try {
    const { data } = await merchantsService.listMyMerchants()
    terminals.value = data
  } catch {
    /* non-critical */
  }
}

onMounted(() => {
  loadTerminals()
  load()
})
</script>
