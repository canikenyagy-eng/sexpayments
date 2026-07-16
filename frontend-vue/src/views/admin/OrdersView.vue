<template>
  <div>
    <PageHeader title="Ордера" />

    <div class="mb-4 grid grid-cols-2 gap-3 md:grid-cols-2 lg:grid-cols-4">
      <BaseSelect v-model="filters.status" label="Статус" :options="statusOptions" />
      <BaseSelect v-model="filters.payment_method" label="Метод" :options="methodOptions" />
      <BaseInput v-model="filters.id_search" label="ID / UUID / external / provider ID" placeholder="поиск по ID" class="col-span-2 md:col-span-1" />
      <BaseInput v-model="filters.trader_login" label="Логин трейдера" placeholder="trader" class="col-span-2 md:col-span-1" />
      <BaseInput v-model="filters.merchant_login" label="Логин мерчанта" placeholder="merchant" class="col-span-2 md:col-span-1" />
      <BaseInput v-model="filters.amount_from" label="Сумма от (RUB)" type="number" placeholder="0" />
      <BaseInput v-model="filters.amount_to" label="Сумма до (RUB)" type="number" placeholder="∞" />
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
      @row-click="onRowClick"
    >
      <template #cell-uuid="{ value }">
        <UuidDisplay :value="value" />
      </template>
      <template #header-provider>
        <Network class="mx-auto block h-4 w-4 text-text-muted" />
      </template>
      <template #cell-provider="{ row }">
        <Network v-if="row.provider_order_id" class="mx-auto block h-4 w-4 text-accent" />
        <span v-else />
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
      <template #cell-merchant_login="{ value }">
        <span class="text-xs">{{ value }}</span>
      </template>
      <template #cell-trader_login="{ value }">
        <span class="text-xs">{{ value }}</span>
      </template>
      <template #cell-created_at="{ value }">{{ formatDate(value) }}</template>
    </DataTable>

    <!-- Order detail modal: see AdminOrderDetailModal for layout. -->
    <AdminOrderDetailModal
      v-model="showOrderInfo"
      :order="selectedOrder"
      :merchant-login="selectedOrder ? getMerchantLogin(selectedOrder.merchant_id) : '—'"
      :trader-login="selectedOrder ? getTraderLogin(selectedOrder.trader_id) : '—'"
      :teamlead-rewards="teamleadRewardRows"
      @edit="selectedOrder && openEditOrder(selectedOrder)"
      @resend="selectedOrder && resendCallback(selectedOrder)"
      @debug="selectedOrder && debugOrder(selectedOrder)"
      @block-client="selectedOrder && blockClient(selectedOrder)"
    />

    <!-- Edit order modal -->
    <BaseModal v-model="showEditOrder" title="Редактирование ордера">
      <div v-if="editingOrder" class="space-y-4">
        <p class="flex items-center gap-2 text-sm text-text-muted">UUID: <UuidDisplay :value="editingOrder.uuid" :truncate="false" show-icon /></p>
        <BaseSelect v-model="orderEditForm.status" label="Статус" :options="allStatusOptions" />
        <!-- Сумму можно менять только при активном диспуте (бэкенд это требует). -->
        <BaseInput
          v-if="editingOrder.status === 'disputed'"
          v-model="orderEditForm.amount"
          label="Сумма (RUB)"
          type="number"
        />
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showEditOrder = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="editSaving" @click="saveOrderEdit">Сохранить</BaseButton>
      </template>
    </BaseModal>

    <AdminOrderDebugModal v-model="showDebug" :data="debugData" :loading="debugLoading" />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
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
import AdminOrderDebugModal from '@/components/modals/AdminOrderDebugModal.vue'
import AdminOrderDetailModal from '@/components/modals/AdminOrderDetailModal.vue'
import { Network } from 'lucide-vue-next'
import { ordersService } from '@/api/services/orders.service'
import { usersService } from '@/api/services/users.service'
import { merchantsService } from '@/api/services/merchants.service'
import { callbacksService } from '@/api/services/callbacks.service'
import { clientsService } from '@/api/services/clients.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { formatDate } from '@/utils/format'
import type { Order, OrderDebug } from '@/types'
import { orderStatusOptions, orderStatusOptionsWithAll, paymentMethodOptionsWithAll } from '@/constants'

const toast = useToast()
const { confirm } = useConfirm()

const loading = ref(false)
const editSaving = ref(false)
const orders = ref<Order[]>([])
const page = ref(1)
const perPage = ref(25)
const totalItems = ref(0)
const totalPages = computed(() => Math.max(1, Math.ceil(totalItems.value / perPage.value)))

const filters = reactive({
  status: '',
  id_search: '',
  trader_login: '',
  merchant_login: '',
  payment_method: '',
  amount_from: '',
  amount_to: '',
})

const usersMap = ref<Record<number, string>>({})
const merchantsMap = ref<Record<number, { user_id: number; name?: string }>>({})

const showEditOrder = ref(false)
const editingOrder = ref<Order | null>(null)
const orderEditForm = reactive({ status: '', amount: '' })

const showOrderInfo = ref(false)
const selectedOrder = ref<Order | null>(null)

const showDebug = ref(false)
const debugLoading = ref(false)
const debugData = ref<OrderDebug | null>(null)

const columns: Column[] = [
  { key: 'uuid', label: 'UUID' },
  { key: 'provider', label: '', width: '1%', align: 'center' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'amount_usdt', label: 'USDT', align: 'right' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'status', label: 'Статус' },
  { key: 'merchant_login', label: 'Мерчант' },
  { key: 'trader_login', label: 'Трейдер' },
  { key: 'created_at', label: 'Создан' },
]

const statusOptions = orderStatusOptionsWithAll
const methodOptions = paymentMethodOptionsWithAll
const allStatusOptions = orderStatusOptions

function getMerchantLogin(merchantId: number): string {
  const m = merchantsMap.value[merchantId]
  if (!m) return `#${merchantId}`
  return usersMap.value[m.user_id] ?? m.name ?? `#${merchantId}`
}

function getTraderLogin(traderId?: number): string {
  if (!traderId) return '—'
  return usersMap.value[traderId] ?? `#${traderId}`
}

// Per-teamlead rewards for the open order, teamlead_id resolved to a login.
const teamleadRewardRows = computed(() =>
  (selectedOrder.value?.financials?.teamlead_rewards ?? []).map(r => ({
    name: usersMap.value[r.teamlead_id] ?? `#${r.teamlead_id}`,
    reward_usdt: r.reward_usdt,
    side: r.side,
  })),
)

// Enrich each order row with resolved merchant/trader logins for the table.
const rows = computed(() =>
  orders.value.map(order => ({
    ...order,
    merchant_login: getMerchantLogin(order.merchant_id),
    trader_login: getTraderLogin(order.trader_id),
  })),
)

function onRowClick(row: Record<string, any>) {
  openOrderInfo(row as Order)
}

function applyFilters() {
  page.value = 1
  load()
}

function resetFilters() {
  filters.status = ''
  filters.id_search = ''
  filters.trader_login = ''
  filters.merchant_login = ''
  filters.payment_method = ''
  filters.amount_from = ''
  filters.amount_to = ''
  page.value = 1
  load()
}

function onPerPageChange(size: number) {
  perPage.value = size
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const params: any = { skip: (page.value - 1) * perPage.value, limit: perPage.value }
    if (filters.status) params.status = filters.status
    if (filters.id_search) params.id_search = filters.id_search.trim()
    if (filters.trader_login) params.trader_login = filters.trader_login.trim()
    if (filters.merchant_login) params.merchant_login = filters.merchant_login.trim()
    if (filters.payment_method) params.payment_method = filters.payment_method
    if (filters.amount_from) params.amount_from = Number(filters.amount_from)
    if (filters.amount_to) params.amount_to = Number(filters.amount_to)

    const { data } = await ordersService.list(params)
    orders.value = data.items
    totalItems.value = data.total

    const [usersRes, merchRes] = await Promise.all([
      usersService.list({ limit: 500 }),
      merchantsService.listAll({ limit: 500 }),
    ])
    const uMap: Record<number, string> = {}
    for (const u of usersRes.data) uMap[u.id] = u.username
    usersMap.value = uMap

    const mMap: Record<number, { user_id: number; name?: string }> = {}
    for (const m of merchRes.data) mMap[m.id] = { user_id: m.user_id, name: m.name }
    merchantsMap.value = mMap
  } catch { toast.error('Ошибка загрузки ордеров') }
  finally { loading.value = false }
}

function openEditOrder(row: Order) {
  editingOrder.value = row
  orderEditForm.status = row.status
  orderEditForm.amount = String(row.amount)
  showOrderInfo.value = false
  showEditOrder.value = true
}

async function saveOrderEdit() {
  if (!editingOrder.value) return
  editSaving.value = true
  try {
    const payload: any = {}
    if (orderEditForm.status !== editingOrder.value.status) payload.status = orderEditForm.status
    if (Number(orderEditForm.amount) !== editingOrder.value.amount) payload.amount = Number(orderEditForm.amount)
    await ordersService.updateAdmin(editingOrder.value.id, payload)
    toast.success('Ордер обновлён')
    showEditOrder.value = false
    load()
  } catch { toast.error('Ошибка обновления') }
  finally { editSaving.value = false }
}

function openOrderInfo(row: Order) {
  selectedOrder.value = row
  showOrderInfo.value = true
}

async function debugOrder(row: Order) {
  showOrderInfo.value = false
  showDebug.value = true
  debugLoading.value = true
  debugData.value = null
  try {
    const { data } = await ordersService.debug(String(row.uuid || row.id))
    debugData.value = data
  } catch { toast.error('Ошибка загрузки debug-данных') }
  finally { debugLoading.value = false }
}

async function resendCallback(row: Order) {
  try {
    await callbacksService.resendForOrder(row.id)
    toast.success('Callback переотправлен')
  } catch { toast.error('Ошибка переотправки') }
}

async function blockClient(row: Order) {
  if (!row.client_user_id) return
  const ok = await confirm({
    title: 'Заблокировать клиента?',
    message: `Клиент ${row.client_user_id} перестанет получать реквизиты — его запросы будут игнорироваться.`,
    variant: 'danger',
    confirmText: 'Заблокировать',
  })
  if (!ok) return
  try {
    await clientsService.block({ merchant_id: row.merchant_id, client_user_id: row.client_user_id })
    toast.success('Клиент заблокирован')
  } catch { toast.error('Не удалось заблокировать клиента') }
}

// Apply `?id_search=<id>` from the URL so deep-links from other views (e.g.
// the "Ордер #X" reference in admin finances) land on a pre-filtered list.
const route = useRoute()
function applyRouteFilters() {
  const q = route.query
  let changed = false
  if (typeof q.id_search === 'string' && q.id_search && filters.id_search !== q.id_search) {
    filters.id_search = q.id_search
    changed = true
  }
  if (changed) {
    page.value = 1
    load()
  }
}

onMounted(() => {
  applyRouteFilters()
  if (!filters.id_search) load()
})

watch(() => route.query.id_search, () => applyRouteFilters())
</script>
