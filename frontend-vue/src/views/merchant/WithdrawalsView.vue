<template>
  <div>
    <PageHeader title="Выводы">
      <template #actions>
        <BaseButton variant="gold" size="sm" @click="openCreateModal">Создать вывод</BaseButton>
      </template>
    </PageHeader>

    <!-- Aggregated balance summary (across every terminal) -->
    <div class="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-3">
      <StatCard label="Доступно (WORK)" :value="`${formatAmount(totalWork)} USDT`" :icon="Wallet" />
      <StatCard label="Заморожено (ESCROW)" :value="`${formatAmount(totalEscrow)} USDT`" :icon="Lock" />
      <StatCard
        label="Терминалов"
        :value="String(terminals.length)"
        :icon="Receipt"
      />
    </div>

    <!-- Filters -->
    <div class="mb-4 flex flex-wrap items-end gap-3">
      <BaseSelect
        v-model="filterStatus"
        label="Статус"
        :options="[
          { value: '', label: 'Все' },
          { value: 'pending', label: 'Ожидание' },
          { value: 'success', label: 'Успешно' },
          { value: 'rejected', label: 'Отклонён' },
        ]"
      />
      <BaseButton variant="dark" size="sm" @click="load">Применить</BaseButton>
    </div>

    <DataTable
      :columns="columns"
      :rows="items"
      :loading="loading"
      row-key="id"
      :current-page="page"
      :total-pages="totalPages"
      @page-change="p => { page = p; load() }"
    >
      <template #cell-scope="{ row }">
        <span
          v-if="row.merchant_id == null"
          class="inline-flex items-center rounded-full bg-accent/15 px-2 py-0.5 text-xs font-bold text-accent"
        >
          Все терминалы
        </span>
        <span v-else class="text-xs text-text-main">
          {{ row.merchant_name || `Терминал #${row.merchant_id}` }}
        </span>
      </template>
      <template #cell-amount="{ row }">
        {{ formatAmount(row.amount) }} {{ row.currency }}
      </template>
      <template #cell-fee_amount="{ value }">
        {{ formatAmount(value) }}
      </template>
      <template #cell-status="{ value }">
        <StatusBadge :status="value" />
      </template>
      <template #cell-created_at="{ value }">
        {{ formatDate(value) }}
      </template>
      <template #cell-rejection_reason="{ value }">
        <span v-if="value" class="text-status-danger text-xs">{{ value }}</span>
        <span v-else class="text-text-muted">—</span>
      </template>
    </DataTable>

    <!-- Create modal -->
    <BaseModal v-model="showCreate" title="Создать заявку на вывод">
      <div class="space-y-4">
        <!-- Terminal picker — shown only when there's an actual choice -->
        <div v-if="terminals.length > 1">
          <BaseSelect
            v-model="selectedTerminalIdStr"
            label="Терминал"
            :options="terminalOptions"
          />
        </div>

        <div class="rounded-lg bg-bg-card p-3 text-sm">
          <p v-if="!singleTerminal" class="text-text-muted">Баланс выбранного терминала:</p>
          <p class="text-lg font-bold text-accent">{{ formatAmount(availableForSelection) }} USDT</p>
          <p v-if="feeHint" class="text-xs text-text-muted mt-1">{{ feeHint }}</p>
        </div>

        <div>
          <BaseInput v-model="createForm.amount" label="Сумма (USDT)" type="number" required />
          <p v-if="Number(createForm.amount) > 0" class="mt-1 text-sm text-text-muted">
            К получению: <span class="font-bold text-accent">{{ formatAmount(netAmount) }} USDT</span>
          </p>
        </div>
        <BaseInput v-model="createForm.destination_address" label="Адрес кошелька" required />
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showCreate = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="creating" @click="createWithdrawal">Создать</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import PageHeader from '@/components/layout/PageHeader.vue'
import StatCard from '@/components/ui/StatCard.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { Wallet, Lock, Receipt } from 'lucide-vue-next'
import { merchantsService } from '@/api/services/merchants.service'
import { financesService } from '@/api/services/finances.service'
import { useToast } from '@/composables/useToast'
import { formatAmount, formatDate } from '@/utils/format'
import type {
  BalanceInfo,
  MerchantListItem,
  WithdrawalRequest,
  WithdrawalStatus,
} from '@/types'

const toast = useToast()
const route = useRoute()
const router = useRouter()
const PER_PAGE = 25

function extractApiError(e: any, fallback: string): string {
  const data = e?.response?.data
  if (!data) return fallback
  const msg = data?.error?.message ?? data?.detail ?? data?.message
  if (typeof msg === 'string' && msg.trim()) return msg
  if (Array.isArray(msg) && msg.length && typeof msg[0]?.msg === 'string') {
    return msg[0].msg
  }
  return fallback
}

const loading = ref(false)
const creating = ref(false)
const items = ref<WithdrawalRequest[]>([])
const page = ref(1)
const totalPages = ref(1)
const filterStatus = ref('')

const balances = ref<BalanceInfo[]>([])
const totalWork = computed(() => {
  const row = balances.value.find(b => b.type === 'work' && b.currency === 'USDT')
  return row ? row.amount : 0
})
const totalEscrow = computed(() => {
  const row = balances.value.find(b => b.type === 'escrow' && b.currency === 'USDT')
  return row ? row.amount : 0
})

type TerminalCard = MerchantListItem & {
  balance_work: number
  balance_escrow: number
  withdrawal_fee_fixed: number
}
const terminals = ref<TerminalCard[]>([])
const selectedTerminalIdStr = ref<string>('')
const selectedTerminalId = computed<number | null>(() =>
  selectedTerminalIdStr.value ? Number(selectedTerminalIdStr.value) : null,
)

const terminalOptions = computed(() =>
  terminals.value.map(t => ({
    value: String(t.id),
    label: `${t.name || `Терминал #${t.id}`} · ${formatAmount(t.balance_work)} USDT`,
  })),
)

const showCreate = ref(false)
const createForm = ref({ amount: '', destination_address: '' })

const availableForSelection = computed(() => {
  const t = terminals.value.find(x => x.id === selectedTerminalId.value)
  return t ? t.balance_work : 0
})

// A merchant with a single terminal has no choice — hide the picker and the
// balance caption, and just withdraw from that terminal.
const singleTerminal = computed(() => terminals.value.length === 1)

// Fee is withheld from the entered amount, so "К получению" = amount − fee.
const applicableFee = computed<number>(() => {
  const t = terminals.value.find(x => x.id === selectedTerminalId.value)
  return t ? (t.withdrawal_fee_fixed || 0) : 0
})

const netAmount = computed<number>(() => {
  const a = Number(createForm.value.amount)
  if (!a || a <= 0) return 0
  return Math.max(0, a - applicableFee.value)
})

const feeHint = computed(() => {
  const t = terminals.value.find(x => x.id === selectedTerminalId.value)
  if (!t) return ''
  return t.withdrawal_fee_fixed > 0
    ? `Комиссия терминала: ${formatAmount(t.withdrawal_fee_fixed)} USDT`
    : 'Комиссия терминала: 0 USDT'
})

const columns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'scope', label: 'Откуда' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'fee_amount', label: 'Комиссия', align: 'right' },
  { key: 'destination_address', label: 'Адрес' },
  { key: 'status', label: 'Статус' },
  { key: 'rejection_reason', label: 'Причина' },
  { key: 'created_at', label: 'Дата' },
]

async function loadBalances() {
  try {
    const { data } = await financesService.listMyBalances()
    balances.value = data
  } catch (e: any) {
    toast.error(extractApiError(e, 'Не удалось загрузить баланс'))
  }
}

async function loadTerminals() {
  try {
    const { data } = await merchantsService.listMyMerchants()
    const cards = await Promise.all(
      data.map(async (m): Promise<TerminalCard> => {
        try {
          const { data: p } = await merchantsService.getMyProfile(m.id)
          return {
            ...m,
            balance_work: p.balance_work ?? 0,
            balance_escrow: p.balance_escrow ?? 0,
            withdrawal_fee_fixed: p.withdrawal_fee_fixed ?? 0,
          }
        } catch {
          return {
            ...m,
            balance_work: 0,
            balance_escrow: 0,
            withdrawal_fee_fixed: 0,
          }
        }
      }),
    )
    terminals.value = cards
    if (!selectedTerminalIdStr.value && cards.length) {
      selectedTerminalIdStr.value = String(cards[0].id)
    }
  } catch (e: any) {
    toast.error(extractApiError(e, 'Не удалось загрузить терминалы'))
  }
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = { skip: (page.value - 1) * PER_PAGE, limit: PER_PAGE }
    if (filterStatus.value) params.status = filterStatus.value as WithdrawalStatus
    const { data } = await merchantsService.listMyWithdrawals(params)
    items.value = data
    totalPages.value = data.length < PER_PAGE ? page.value : page.value + 1
  } catch {
    toast.error('Ошибка загрузки выводов')
  } finally {
    loading.value = false
  }
}

function openCreateModal() {
  createForm.value = { amount: '', destination_address: '' }
  showCreate.value = true
  loadBalances()
  loadTerminals()
}

async function createWithdrawal() {
  const amount = Number(createForm.value.amount)
  if (!amount || amount <= 0) {
    toast.error('Укажите сумму')
    return
  }
  if (amount <= applicableFee.value) {
    toast.error('Сумма должна быть больше комиссии терминала')
    return
  }
  if (!createForm.value.destination_address || createForm.value.destination_address.length < 10) {
    toast.error('Укажите корректный адрес кошелька')
    return
  }
  if (!selectedTerminalId.value) {
    toast.error('Выберите терминал')
    return
  }
  creating.value = true
  try {
    await merchantsService.createWithdrawal(
      { amount, destination_address: createForm.value.destination_address },
      { merchantId: selectedTerminalId.value },
    )
    toast.success('Заявка на вывод создана')
    showCreate.value = false
    loadBalances()
    loadTerminals()
    load()
  } catch (e: any) {
    toast.error(extractApiError(e, 'Ошибка создания вывода'))
  } finally {
    creating.value = false
  }
}

async function consumeCreateQuery() {
  if (route.query.create !== '1') return
  openCreateModal()
  const { create: _omit, ...rest } = route.query
  router.replace({ query: rest })
}

onMounted(() => {
  loadBalances()
  loadTerminals()
  load()
  consumeCreateQuery()
})

watch(() => route.query.create, () => consumeCreateQuery())
</script>
