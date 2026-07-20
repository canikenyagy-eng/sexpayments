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

    <section class="mb-5 grid gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(380px,0.72fr)]">
      <div class="relative overflow-hidden rounded-[1.25rem] border border-accent/15 bg-bg-surface/70 p-5 shadow-[0_22px_70px_rgba(0,0,0,0.28),inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl">
        <div class="pointer-events-none absolute inset-0 sp-panel-grid opacity-35" />
        <div class="relative flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p class="sp-kicker">Операционный поток</p>
            <h2 class="mt-2 text-2xl font-black leading-none text-text-main">Контроль входящих ордеров</h2>
            <p class="mt-3 max-w-2xl text-sm font-semibold leading-6 text-text-muted">
              Поиск, статусы и терминалы собраны в одном рабочем контуре для быстрой сверки.
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
        <div class="mb-4 flex items-center justify-between gap-3">
          <div>
            <p class="sp-kicker">Фильтры</p>
            <h2 class="mt-2 text-xl font-black leading-none text-text-main">Поиск операций</h2>
          </div>
          <span class="rounded-lg border border-accent/15 bg-bg-main/45 px-2.5 py-1 text-xs font-black text-text-muted">
            {{ activeFilterCount }} акт.
          </span>
        </div>
        <div class="grid gap-3 sm:grid-cols-2">
          <BaseSelect v-model="filters.merchant_id" label="Терминал" :options="terminalFilterOptions" />
          <BaseSelect v-model="filters.status" label="Статус" :options="statusOptions" />
          <BaseSelect v-model="filters.payment_method" label="Метод" :options="methodOptions" />
          <BaseInput v-model="filters.search" label="Поиск" placeholder="UUID или внешний ID" />
        </div>
        <div class="mt-4 flex flex-wrap items-center justify-end gap-2">
          <BaseButton v-if="activeFilterCount" variant="ghost" size="sm" @click="resetFilters">Сбросить</BaseButton>
          <BaseButton variant="dark" size="sm" @click="page = 1; load()">Применить</BaseButton>
        </div>
      </div>
    </section>

    <DataTable
      :columns="columns"
      :rows="orders"
      :loading="loading"
      row-key="id"
      :current-page="page"
      :total-pages="totalPages"
      :total-items="totalItems"
      :per-page="perPage"
      empty-text="Ордеров по выбранным параметрам нет"
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
        <BaseBadge :color="value === 'payin' ? 'success' : 'warning'">
          {{ value === 'payin' ? 'Вход' : 'Выход' }}
        </BaseBadge>
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
        <BaseInput v-model="createForm.internalId" label="Внутренний ID (опционально)" />
        <BaseInput v-model="createForm.notificationUrl" label="URL уведомлений (опционально)" />
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
          <p><span class="text-text-muted">Ссылка на оплату:</span></p>
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
const activeFilterCount = computed(() =>
  [filters.status, filters.payment_method, filters.search.trim(), filters.merchant_id].filter(Boolean).length,
)
const summaryRows = computed(() => [
  { label: 'Всего', value: totalItems.value },
  { label: 'На странице', value: orders.value.length },
  { label: 'Терминалы', value: terminalFilterOptions.value.length - 1 },
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
  { key: 'external_id', label: 'Внешний ID' },
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

function resetFilters() {
  filters.status = ''
  filters.payment_method = ''
  filters.search = ''
  filters.merchant_id = ''
  page.value = 1
  load()
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
