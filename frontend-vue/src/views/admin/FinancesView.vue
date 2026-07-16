<template>
  <div>
    <PageHeader title="Финансы">
      <template #actions>
        <div class="flex flex-wrap gap-2">
          <BaseButton variant="gold" size="sm" @click="openAdjust">
            <Plus class="mr-1.5 h-4 w-4" />
            Корректировка баланса
          </BaseButton>
          <BaseButton variant="gold" size="sm" @click="showHashDeposit = true">
            <Coins class="mr-1.5 h-4 w-4" />
            Пополнение хэшем
          </BaseButton>
        </div>
      </template>
    </PageHeader>

    <div class="mb-4 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
      <BaseSelect v-model="filters.reference_type" label="Тип операции" :options="typeOptions" />
      <BaseInput v-model="filters.order_search" label="Ордер" placeholder="ID / UUID / external" />
      <BaseInput v-model="filters.user_login" label="Логин пользователя" placeholder="trader / merchant / teamlead" />
      <div class="grid grid-cols-2 gap-2">
        <BaseInput v-model="filters.amount_from" label="Сумма от" type="number" placeholder="0" />
        <BaseInput v-model="filters.amount_to" label="Сумма до" type="number" placeholder="∞" />
      </div>
      <div class="flex items-end gap-2 lg:col-span-4">
        <BaseButton variant="gold" size="sm" @click="applyFilters">Применить</BaseButton>
        <BaseButton variant="dark" size="sm" @click="resetFilters">Сброс</BaseButton>
      </div>
    </div>

    <DataTable
      :columns="columns"
      :rows="entries"
      :loading="loading"
      row-key="id"
      :current-page="page"
      :total-pages="totalPages"
      :per-page="perPage"
      @page-change="p => { page = p; load() }"
      @per-page-change="n => { perPage = n; page = 1; load() }"
    >
      <template #cell-amount="{ row }">
        <span class="font-mono text-sm">{{ row.amount }} {{ row.currency }}</span>
      </template>

      <template #cell-reference_type="{ value }">
        <BaseBadge :color="typeColor(value as string)">{{ formatType(value as string) }}</BaseBadge>
      </template>

      <template #cell-counterparty="{ row }">
        <span v-if="counterparty(row as LedgerEntry)" class="text-sm">
          {{ counterparty(row as LedgerEntry)?.owner_label }}
          <span class="ml-1 text-xs text-text-muted">· {{ counterpartyRole(row as LedgerEntry) }}</span>
        </span>
        <span v-else class="text-text-muted">—</span>
      </template>

      <template #cell-from_balance="{ row }">
        <BalanceBadge :balance="(row as LedgerEntry).from_balance" />
      </template>

      <template #cell-to_balance="{ row }">
        <BalanceBadge :balance="(row as LedgerEntry).to_balance" />
      </template>

      <template #cell-reference="{ row }">
        <RouterLink
          v-if="refLabel(row as LedgerEntry) && refLinkHref(row as LedgerEntry)"
          :to="refLinkHref(row as LedgerEntry)!"
          class="text-sm text-accent hover:underline"
        >
          {{ refLabel(row as LedgerEntry) }}
        </RouterLink>
        <span v-else-if="refLabel(row as LedgerEntry)" class="text-sm text-text-muted">
          {{ refLabel(row as LedgerEntry) }}
        </span>
        <span v-else class="text-text-muted">—</span>
      </template>

      <template #cell-created_at="{ value }">
        {{ formatDate(value) }}
      </template>
    </DataTable>

    <BalanceAdjustModal
      :open="showAdjust"
      @update:open="showAdjust = $event"
      @success="load"
    />

    <HashDepositModal
      :open="showHashDeposit"
      @update:open="showHashDeposit = $event"
      @success="load"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted, h } from 'vue'
import { RouterLink } from 'vue-router'
import { Plus, Coins } from 'lucide-vue-next'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BalanceAdjustModal from '@/components/modals/BalanceAdjustModal.vue'
import HashDepositModal from '@/components/modals/HashDepositModal.vue'
import { financesService } from '@/api/services/finances.service'
import { useToast } from '@/composables/useToast'
import { formatDate } from '@/utils/format'
import type { BalanceRefInfo, LedgerEntry } from '@/types'

const toast = useToast()

const BalanceBadge = (props: { balance?: BalanceRefInfo | null }) => {
  const b = props.balance
  if (!b) return h('span', { class: 'text-text-muted' }, '—')
  const typeLabel = b.type === 'work' ? 'WORK' : b.type === 'escrow' ? 'ESCROW' : 'SAFE'
  const typeColor =
    b.type === 'work'
      ? 'bg-status-success/15 text-status-success border-status-success/30'
      : b.type === 'escrow'
        ? 'bg-status-warning/15 text-status-warning border-status-warning/30'
        : 'bg-bg-hover text-text-muted border-border'
  return h(
    'span',
    {
      class: `inline-flex items-center gap-1 rounded-md border px-1.5 py-0.5 text-[11px] font-semibold ${typeColor}`,
      title: b.owner_label,
    },
    `${typeLabel} · ${b.currency}`,
  )
}

const loading = ref(false)
const entries = ref<LedgerEntry[]>([])
const page = ref(1)
const perPage = ref(50)
const totalPages = ref(1)

const filters = reactive({
  reference_type: '',
  order_search: '',
  user_login: '',
  amount_from: '',
  amount_to: '',
})

const showAdjust = ref(false)
const showHashDeposit = ref(false)

const columns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'reference_type', label: 'Тип' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'counterparty', label: 'User' },
  { key: 'from_balance', label: 'Из' },
  { key: 'to_balance', label: 'В' },
  { key: 'reference', label: 'Источник' },
  { key: 'description', label: 'Описание' },
  { key: 'created_at', label: 'Дата' },
]

const typeOptions = [
  { value: '', label: 'Все' },
  { value: 'order_payin', label: 'Пополнение (payin)' },
  { value: 'order_payout', label: 'Выплата (payout)' },
  { value: 'system_commission', label: 'Комиссия системы' },
  { value: 'deposit', label: 'Депозит' },
  { value: 'crypto_deposit', label: 'Пополнение (крипто)' },
  { value: 'withdrawal', label: 'Вывод' },
  { value: 'dispute_refund', label: 'Возврат (спор)' },
  { value: 'internal_transfer', label: 'Внутренний перевод' },
  { value: 'teamlead_reward', label: 'Награда тимлида' },
  { value: 'trader_reward', label: 'Награда трейдера' },
]

function formatType(type: string): string {
  const opt = typeOptions.find(t => t.value === type)
  return opt?.label ?? type
}

type BadgeColor = 'success' | 'default' | 'danger' | 'gold' | 'info' | 'warning'

function typeColor(type: string): BadgeColor {
  const colors: Record<string, BadgeColor> = {
    order_payin: 'success',
    order_payout: 'warning',
    deposit: 'info',
    crypto_deposit: 'info',
    withdrawal: 'danger',
    internal_transfer: 'gold',
    system_commission: 'default',
    dispute_refund: 'danger',
    teamlead_reward: 'info',
    trader_reward: 'info',
  }
  return colors[type] ?? 'default'
}

function counterparty(row: LedgerEntry): BalanceRefInfo | null {
  const from = row.from_balance ?? null
  const to = row.to_balance ?? null
  if (from && from.owner_kind !== 'system') {
    if (!to || to.owner_kind === 'system') return from
  }
  if (to && to.owner_kind !== 'system') return to
  return from ?? to
}

const ROLE_LABELS: Record<string, string> = {
  merchant: 'Merchant',
  trader: 'Trader',
  teamlead: 'Agent',
  admin: 'Admin',
  system: 'System',
  user: 'User',
}

function counterpartyRole(row: LedgerEntry): string {
  const cp = counterparty(row)
  if (!cp) return ''
  return ROLE_LABELS[cp.owner_kind] ?? cp.owner_kind
}

function refLabel(row: LedgerEntry): string {
  const t = row.reference_type
  if (!row.reference_id) return ''
  if (t === 'withdrawal') return `Вывод #${row.reference_id}`
  if (t === 'deposit') return `Депозит #${row.reference_id}`
  if (t === 'crypto_deposit') return 'Крипто-пополнение'
  if (
    t === 'order_payin' ||
    t === 'order_payout' ||
    t === 'system_commission' ||
    t === 'teamlead_reward' ||
    t === 'trader_reward' ||
    t === 'dispute_refund'
  ) {
    return `Ордер #${row.reference_id}`
  }
  return row.reference_id
}

function refLinkHref(row: LedgerEntry): { path: string; query?: Record<string, string> } | null {
  const t = row.reference_type
  if (!row.reference_id) return null
  if (t === 'withdrawal') return { path: '/admin/withdrawals' }
  if (
    t === 'order_payin' ||
    t === 'order_payout' ||
    t === 'system_commission' ||
    t === 'teamlead_reward' ||
    t === 'trader_reward' ||
    t === 'dispute_refund'
  ) {
    return { path: '/admin/orders', query: { id_search: row.reference_id } }
  }
  return null
}

function openAdjust() {
  showAdjust.value = true
}

function applyFilters() {
  page.value = 1
  load()
}

function resetFilters() {
  filters.reference_type = ''
  filters.order_search = ''
  filters.user_login = ''
  filters.amount_from = ''
  filters.amount_to = ''
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const params: any = { skip: (page.value - 1) * perPage.value, limit: perPage.value }
    if (filters.reference_type) params.reference_type = filters.reference_type
    if (filters.order_search) params.order_search = filters.order_search
    if (filters.user_login) params.user_login = filters.user_login
    if (filters.amount_from) params.amount_from = Number(filters.amount_from)
    if (filters.amount_to) params.amount_to = Number(filters.amount_to)

    const { data } = await financesService.listLedger(params)
    entries.value = data
    totalPages.value = data.length < perPage.value ? page.value : page.value + 1
  } catch {
    toast.error('Ошибка загрузки')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
