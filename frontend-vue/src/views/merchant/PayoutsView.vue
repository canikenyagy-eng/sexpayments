<template>
  <div>
    <PageHeader title="Выплаты" />

    <section class="mb-5 grid gap-4 xl:grid-cols-[minmax(0,0.95fr)_minmax(360px,0.72fr)]">
      <div class="relative overflow-hidden rounded-[1.25rem] border border-accent/15 bg-bg-surface/70 p-5 shadow-[0_22px_70px_rgba(0,0,0,0.28),inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl">
        <div class="pointer-events-none absolute inset-0 sp-panel-grid opacity-35" />
        <div class="relative flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <p class="sp-kicker">Выплатный контур</p>
            <h2 class="mt-2 text-2xl font-black leading-none text-text-main">Очередь исходящих выплат</h2>
            <p class="mt-3 max-w-2xl text-sm font-semibold leading-6 text-text-muted">
              Статусы, методы и идентификаторы выплат собраны для контроля исходящего потока.
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
            <h2 class="mt-2 text-xl font-black leading-none text-text-main">Сверка выплат</h2>
          </div>
          <span class="rounded-lg border border-accent/15 bg-bg-main/45 px-2.5 py-1 text-xs font-black text-text-muted">
            {{ activeFilterCount }} акт.
          </span>
        </div>
        <div class="grid gap-3 sm:grid-cols-2">
          <BaseSelect v-model="filters.status" label="Статус" :options="statusOptions" />
          <BaseSelect v-model="filters.payment_method" label="Метод" :options="methodOptions" />
          <BaseInput v-model="filters.search" label="Поиск" placeholder="UUID или внешний ID" class="sm:col-span-2" />
        </div>
        <div class="mt-4 flex flex-wrap items-center justify-end gap-2">
          <BaseButton v-if="activeFilterCount" variant="ghost" size="sm" @click="resetFilters">Сбросить</BaseButton>
          <BaseButton variant="dark" size="sm" @click="page = 1; load()">Применить</BaseButton>
        </div>
      </div>
    </section>

    <DataTable
      :columns="columns"
      :rows="payouts"
      :loading="loading"
      row-key="id"
      :current-page="page"
      :total-pages="totalPages"
      :total-items="totalItems"
      :per-page="perPage"
      empty-text="Выплат по выбранным параметрам нет"
      clickable
      @page-change="p => { page = p; load() }"
      @per-page-change="(n: number) => { perPage = n; page = 1; load() }"
      @row-click="openDetails"
    >
      <template #cell-id="{ value }">
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
      <template #cell-terminal_name="{ row }">
        <span class="text-xs text-text-main">
          {{ row.terminal_name || `Терминал #${row.payout_terminal_id}` }}
        </span>
      </template>
      <template #cell-amount="{ row }">
        <span class="font-bold text-text-main">{{ formatAmount(row.amount) }}</span>
        <span class="ml-1 text-text-muted">{{ row.currency }}</span>
      </template>
      <template #cell-amount_usdt="{ value }">
        {{ value != null ? formatAmount(value) + ' USDT' : '—' }}
      </template>
      <template #cell-payment_method="{ value }">
        <MethodBadge :method="value" />
      </template>
      <template #cell-status="{ row }">
        <StatusBadge :status="row.status" :expires-at="row.status === 'created' ? row.expires_at : null" />
      </template>
      <template #cell-created_at="{ value }">
        {{ formatDate(value) }}
      </template>
    </DataTable>

    <!-- Payout details modal -->
    <BaseModal v-model="showDetails" title="Выплата" size="lg">
      <div v-if="detail" class="space-y-4 max-h-[70vh] overflow-y-auto">
        <div class="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
          <div class="sm:col-span-2">
            <span class="text-text-muted">UUID:</span>
            <UuidDisplay :value="detail.id" :truncate="false" show-icon class="ml-1" />
          </div>
          <div><span class="text-text-muted">Внешний ID:</span> <span class="ml-1 text-text-main">{{ detail.external_id || '—' }}</span></div>
          <div><span class="text-text-muted">Терминал:</span> <span class="ml-1 text-text-main">{{ detail.terminal_name || `#${detail.payout_terminal_id}` }}</span></div>
          <div>
            <span class="text-text-muted">Сумма:</span>
            <span class="ml-1 font-bold text-text-main">{{ formatAmount(detail.amount) }} {{ detail.currency }}</span>
          </div>
          <div><span class="text-text-muted">USDT:</span> <span class="ml-1 text-text-main">{{ detail.amount_usdt != null ? formatAmount(detail.amount_usdt) : '—' }}</span></div>
          <div><span class="text-text-muted">Комиссия:</span> <span class="ml-1 text-text-main">{{ detail.merchant_fee_usdt != null ? formatAmount(detail.merchant_fee_usdt) + ' USDT' : '—' }}</span></div>
          <div><span class="text-text-muted">Метод:</span> <MethodBadge :method="detail.payment_method" /></div>
          <div><span class="text-text-muted">Статус:</span> <StatusBadge :status="detail.status" /></div>
          <div><span class="text-text-muted">Получатель:</span> <span class="ml-1 text-text-main">{{ detail.req_holder || '—' }}</span></div>
          <div class="sm:col-span-2">
            <span class="text-text-muted">Реквизит:</span>
            <span class="ml-1 font-mono text-text-main">{{ detail.req_number || '—' }}</span>
            <span v-if="detail.req_extra" class="ml-1 text-text-muted">· {{ detail.req_extra }}</span>
          </div>
          <div><span class="text-text-muted">ID клиента:</span> <span class="ml-1 text-text-main">{{ detail.client_user_id || '—' }}</span></div>
          <div><span class="text-text-muted">Создана:</span> <span class="ml-1 text-text-main">{{ formatDate(detail.created_at) }}</span></div>
          <div><span class="text-text-muted">Истекает:</span> <span class="ml-1 text-text-main">{{ detail.expires_at ? formatDate(detail.expires_at) : '—' }}</span></div>
          <div><span class="text-text-muted">Завершена:</span> <span class="ml-1 text-text-main">{{ detail.completed_at ? formatDate(detail.completed_at) : '—' }}</span></div>
          <div v-if="detail.rejection_reason" class="sm:col-span-2">
            <span class="text-text-muted">Причина отказа:</span>
            <span class="ml-1 text-status-danger">{{ detail.rejection_reason }}</span>
          </div>
        </div>
      </div>
      <template #footer>
        <BaseButton
          v-if="detail && detail.status === 'created'"
          variant="gold"
          :loading="canceling"
          @click="cancelPayout(detail)"
        >
          Отменить
        </BaseButton>
        <BaseButton variant="dark" @click="showDetails = false">Закрыть</BaseButton>
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
import { Copy } from 'lucide-vue-next'
import { merchantsService } from '@/api/services/merchants.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { formatAmount, formatDate, copyToClipboard } from '@/utils/format'
import { payoutStatusOptionsWithAll, paymentMethodOptionsWithAll } from '@/constants'
import type { MerchantPayout } from '@/types'

const toast = useToast()
const { confirm: askConfirm } = useConfirm()

const loading = ref(false)
const canceling = ref(false)
const payouts = ref<MerchantPayout[]>([])
const page = ref(1)
const perPage = ref(25)
const totalItems = ref(0)
const totalPages = computed(() => Math.max(1, Math.ceil(totalItems.value / perPage.value)))
const filters = reactive({ status: '', payment_method: '', search: '' })
const activeFilterCount = computed(() =>
  [filters.status, filters.payment_method, filters.search.trim()].filter(Boolean).length,
)
const pageAmountUsdt = computed(() =>
  payouts.value.reduce((sum, payout) => sum + Number(payout.amount_usdt ?? 0), 0),
)
const summaryRows = computed(() => [
  { label: 'Всего', value: totalItems.value },
  { label: 'На странице', value: payouts.value.length },
  { label: 'Объем', value: `${formatAmount(pageAmountUsdt.value)} USDT` },
])

const showDetails = ref(false)
const detail = ref<MerchantPayout | null>(null)

const columns: Column[] = [
  { key: 'id', label: 'UUID' },
  { key: 'external_id', label: 'Внешний ID' },
  { key: 'terminal_name', label: 'Терминал' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'amount_usdt', label: 'USDT', align: 'right' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Создана' },
]

const statusOptions = payoutStatusOptionsWithAll
const methodOptions = paymentMethodOptionsWithAll

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
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = { skip: (page.value - 1) * perPage.value, limit: perPage.value }
    if (filters.status) params.status = filters.status
    if (filters.payment_method) params.payment_method = filters.payment_method
    if (filters.search.trim()) params.search = filters.search.trim()
    const { data } = await merchantsService.listMyPayouts(params)
    payouts.value = data.items
    totalItems.value = data.total
  } catch {
    toast.error('Ошибка загрузки выплат')
  } finally {
    loading.value = false
  }
}

function openDetails(row: Record<string, any>) {
  detail.value = row as MerchantPayout
  showDetails.value = true
}

async function cancelPayout(p: MerchantPayout) {
  const ok = await askConfirm({
    title: 'Отменить выплату',
    message: `Отменить невзятую выплату на ${formatAmount(p.amount)} ${p.currency}? Средства вернутся на баланс терминала.`,
    confirmText: 'Отменить выплату',
    cancelText: 'Назад',
    variant: 'danger',
  })
  if (!ok) return
  canceling.value = true
  try {
    await merchantsService.cancelMyPayout(p.id)
    toast.success('Выплата отменена')
    showDetails.value = false
    load()
  } catch (e: any) {
    toast.error(e?.response?.data?.message || e?.message || 'Не удалось отменить')
  } finally {
    canceling.value = false
  }
}

onMounted(load)
</script>
