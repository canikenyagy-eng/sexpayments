<template>
  <div>
    <PageHeader title="Мерчанты">
      <template #actions>
        <BaseButton variant="gold" @click="openBalanceModal">Корректировка баланса</BaseButton>
      </template>
    </PageHeader>

    <div class="mb-4 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
      <BaseInput v-model="filters.search" label="Поиск" placeholder="ID или логин" />
      <BaseSelect v-model="filters.status" label="Статус" :options="statusFilterOptions" />
      <BaseSelect v-model="filters.is_active" label="Активен" :options="tristateOptions" />
      <BaseSelect v-model="filters.payment_method" label="Метод" :options="methodFilterOptions" />
      <div class="flex items-end gap-2 lg:col-span-4">
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
      @row-click="goToDetail"
      @page-change="p => { page = p; load() }"
      @per-page-change="n => { perPage = n; page = 1; load() }"
    >
      <template #cell-status="{ value }">
        <StatusBadge :status="value" context="merchant" />
      </template>
      <template #cell-balance="{ value }">
        <span class="font-mono text-xs">{{ value ?? '—' }}</span>
      </template>
      <template #cell-method_display="{ row }">
        <div class="flex flex-wrap gap-1">
          <MethodBadge v-for="m in row.method_list" :key="m" :method="m" />
          <span v-if="!row.method_list.length" class="text-text-muted">—</span>
        </div>
      </template>
      <template #cell-fee_display="{ value }">
        <span class="text-xs">{{ value || '—' }}</span>
      </template>
      <template #cell-teamlead_name="{ value }">
        <span class="text-xs">{{ value || '—' }}</span>
      </template>
      <template #cell-tl_fee="{ value }">
        <span class="text-xs">{{ value || '—' }}</span>
      </template>
    </DataTable>

    <BalanceAdjustModal
      :open="showBalanceModal"
      :initial-merchant-id="balanceInitialMerchantId"
      :allowed-types="[{ value: 'merchant', label: 'Мерчант' }]"
      :lock-entity-type="!!balanceInitialMerchantId"
      @update:open="showBalanceModal = $event"
      @success="load"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import BalanceAdjustModal from '@/components/modals/BalanceAdjustModal.vue'
import { merchantsService } from '@/api/services/merchants.service'
import { usersService } from '@/api/services/users.service'
import { financesService } from '@/api/services/finances.service'
import { teamleadsService } from '@/api/services/teamleads.service'
import { useToast } from '@/composables/useToast'
import type { MerchantAdmin, User, TeamleadLink, BalanceInfo } from '@/types'
import {
  merchantStatusOptionsWithAll,
  paymentMethodOptionsWithAll,
  tristateOptions,
} from '@/constants'

const toast = useToast()
const router = useRouter()

const loading = ref(false)
const merchants = ref<MerchantAdmin[]>([])
const usersMap = ref<Record<number, User>>({})
const balances = ref<BalanceInfo[]>([])
const teamleadLinks = ref<TeamleadLink[]>([])
const teamleadUsers = ref<Record<number, User>>({})
const page = ref(1)
const perPage = ref(25)
const totalPages = ref(1)

const filters = reactive({
  search: '',
  status: '',
  is_active: '' as '' | 'true' | 'false',
  payment_method: '',
})

const showBalanceModal = ref(false)
const balanceInitialMerchantId = ref<number | null>(null)

const columns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'username', label: 'Логин' },
  { key: 'method_display', label: 'Методы' },
  { key: 'fee_display', label: 'Финконфиг' },
  { key: 'teamlead_name', label: 'Тимлид' },
  { key: 'tl_fee', label: 'ФК тимлида' },
  { key: 'currency', label: 'Валюта' },
  { key: 'balance', label: 'Баланс' },
  { key: 'status', label: 'Статус' },
]

const statusFilterOptions = merchantStatusOptionsWithAll
const methodFilterOptions = paymentMethodOptionsWithAll

const rows = computed(() =>
  merchants.value.map((m) => {
    const user = usersMap.value[m.user_id]
    const bal = balances.value.find(b => b.merchant_id === m.id && b.type === 'work')
    const link = teamleadLinks.value.find(l => l.linked_entity_type === 'merchant' && l.linked_entity_id === m.id)
    const tlUser = link ? teamleadUsers.value[link.teamlead_id] : null
    const fees = m.fees || {}
    const methods = Object.keys(fees)

    return {
      ...m,
      username: user?.username ?? '—',
      method_list: methods,
      fee_display: methods.length ? methods.map(k => `${k}: ${fees[k]}%`).join(', ') : '—',
      teamlead_name: tlUser?.username ?? (link ? `ID:${link.teamlead_id}` : '—'),
      tl_fee: link ? `${link.fee_percent}%` : '—',
      currency: 'RUB',
      balance: bal ? bal.amount : null,
    }
  })
)

function applyFilters() {
  page.value = 1
  load()
}

function resetFilters() {
  filters.search = ''
  filters.status = ''
  filters.is_active = ''
  filters.payment_method = ''
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const params: any = { skip: (page.value - 1) * perPage.value, limit: perPage.value }
    if (filters.search) params.search = filters.search
    if (filters.status) params.status = filters.status
    if (filters.is_active) params.is_active = filters.is_active === 'true'
    if (filters.payment_method) params.payment_method = filters.payment_method

    const { data } = await merchantsService.listAll(params)
    merchants.value = data
    totalPages.value = data.length < perPage.value ? page.value : page.value + 1

    const [usersRes, balRes, linksRes] = await Promise.all([
      usersService.list({ role: 'merchant', limit: 500 }),
      financesService.listBalances({ limit: 2000 }),
      teamleadsService.listAllLinks({ limit: 500 }),
    ])

    const uMap: Record<number, User> = {}
    for (const u of usersRes.data) uMap[u.id] = u
    usersMap.value = uMap
    balances.value = balRes.data
    teamleadLinks.value = linksRes.data

    const tlIds = [...new Set(linksRes.data.map(l => l.teamlead_id))]
    if (tlIds.length) {
      const { data: tlUsers } = await usersService.list({ role: 'teamlead', limit: 200 })
      const tMap: Record<number, User> = {}
      for (const u of tlUsers) tMap[u.id] = u
      teamleadUsers.value = tMap
    }
  } catch { toast.error('Ошибка загрузки') }
  finally { loading.value = false }
}

// Row click → full-page detail view. Replaces the previous edit modal.
function goToDetail(row: any) {
  router.push({ name: 'admin-merchant-detail', params: { id: String(row.id) } })
}

function openBalanceModal() {
  balanceInitialMerchantId.value = null
  showBalanceModal.value = true
}

onMounted(load)
</script>
