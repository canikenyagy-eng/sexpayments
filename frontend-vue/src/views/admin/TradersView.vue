<template>
  <div>
    <PageHeader title="Трейдеры">
      <template #actions>
        <BaseButton variant="gold" @click="openBalanceModal">Корректировка баланса</BaseButton>
      </template>
    </PageHeader>

    <div class="mb-4 flex flex-wrap items-end gap-3">
      <BaseInput v-model="searchQuery" label="Поиск" placeholder="ID или логин" class="w-48" />
      <BaseButton variant="dark" size="sm" @click="applyFilters">Применить</BaseButton>
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
      @row-click="openDetail"
      @page-change="p => { page = p; load() }"
      @per-page-change="n => { perPage = n; page = 1; load() }"
    >
      <template #cell-status="{ value }">
        <StatusBadge :status="value" />
      </template>
      <template #cell-payin_method="{ value }">
        <BaseBadge v-if="value" color="success">{{ value }}</BaseBadge>
        <span v-else class="text-text-muted">—</span>
      </template>
      <template #cell-payout_display="{ value }">
        <StatusBadge :status="value ? 'active' : 'inactive'" />
      </template>
      <template #cell-balance="{ value }">
        <span class="font-mono text-xs">{{ value ?? '—' }}</span>
      </template>
      <template #cell-safe_deposit="{ value }">
        <span class="font-mono text-xs">{{ value ?? '—' }}</span>
      </template>
    </DataTable>

    <!-- Detail / Edit modal -->
    <BaseModal v-model="showDetail" :title="`Трейдер: ${detailUsername || ('#' + (detail?.id ?? ''))}`" size="lg">
      <div v-if="detail" class="space-y-5">
        <div class="grid grid-cols-2 gap-4 text-sm">
          <div><span class="text-text-muted">ID:</span> <strong class="text-text-main">{{ detail.id }}</strong></div>
          <div><span class="text-text-muted">Логин:</span> <strong class="text-text-main">{{ detailUsername }}</strong></div>
          <div><span class="text-text-muted">Payin:</span> <strong class="text-text-main">{{ detailPayinMethod || '—' }}</strong></div>
          <div><span class="text-text-muted">Payout:</span> <strong class="text-text-main">{{ detail.is_payout_active ? 'Подключён' : 'Нет' }}</strong></div>
        </div>

        <BaseTabs v-model="activeTab" :tabs="tabs">
          <template #profile>
            <div class="space-y-4 pt-2">
              <BaseSelect v-model="editForm.status" label="Статус" :options="statusOptions" />
              <div>
                <BaseInput
                  v-model="editForm.telegram_group_id"
                  label="Telegram group ID"
                  placeholder="-1001234567890"
                />
                <p class="mt-1 text-xs text-text-muted">
                  Добавьте бота в группу трейдера и отправьте команду /id, чтобы узнать ID.
                </p>
              </div>
              <BaseInput v-model="editForm.priority_bonus_percent" label="Priority bonus %" type="number" placeholder="0" />
            </div>
          </template>

          <template #methods>
            <div class="space-y-3 pt-2">
              <div class="flex items-center justify-between">
                <h4 class="text-sm font-bold text-text-main">Конфигурация методов</h4>
                <BaseButton variant="ghost" size="sm" @click="openDetailBalance">Корректировка баланса</BaseButton>
              </div>
              <div
                v-for="method in ALL_METHODS"
                :key="method"
                class="rounded-xl bg-bg-card p-3"
              >
                <div class="mb-3 flex items-center justify-between">
                  <MethodBadge :method="method" />
                  <button
                    type="button"
                    class="relative inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full transition-colors"
                    :class="methodsMap[method].is_active ? 'bg-accent' : 'bg-bg-surface'"
                    @click="toggleMethod(method)"
                  >
                    <span
                      class="inline-block h-5 w-5 transform rounded-full bg-white transition-transform"
                      :class="methodsMap[method].is_active ? 'translate-x-5' : 'translate-x-0.5'"
                    />
                  </button>
                </div>
                <div class="grid grid-cols-3 gap-2" :class="!methodsMap[method].is_active ? 'opacity-50' : ''">
                  <BaseInput v-model="methodsMap[method].fee" label="Fee %" type="number" :disabled="!methodsMap[method].is_active" />
                  <BaseInput v-model="methodsMap[method].min_amount" label="Min" type="number" :disabled="!methodsMap[method].is_active" />
                  <BaseInput v-model="methodsMap[method].max_amount" label="Max" type="number" :disabled="!methodsMap[method].is_active" />
                </div>
              </div>
            </div>
          </template>

          <template #access>
            <div class="space-y-4 pt-2">
              <div class="rounded-xl border border-border bg-bg-card p-3">
                <div class="flex items-start justify-between gap-3">
                  <div>
                    <div class="text-sm font-bold text-text-main">Принимать ордера от всех мерчантов</div>
                    <p class="mt-1 text-xs text-text-muted">
                      Если выключено — трейдер обслуживает только явно привязанных мерчантов и группы.
                      Без привязок ордера не идут.
                    </p>
                  </div>
                  <BaseSwitch v-model="editForm.accept_all_merchants" />
                </div>
              </div>

              <BaseTransferList
                v-model="editForm.merchant_ids"
                :options="merchantOptions"
                label="Мерчанты"
                available-label="Доступные"
                selected-label="Привязанные"
                search-placeholder="Поиск по названию или ID"
                :disabled="editForm.accept_all_merchants"
              />

              <BaseTransferList
                v-model="editForm.group_ids"
                :options="groupOptions"
                label="Группы"
                available-label="Доступные"
                selected-label="Привязанные"
                search-placeholder="Поиск по группам"
                :disabled="editForm.accept_all_merchants"
              />
            </div>
          </template>
        </BaseTabs>
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showDetail = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="saving" @click="saveTrader">Сохранить</BaseButton>
      </template>
    </BaseModal>

    <BalanceAdjustModal
      :open="showBalanceModal"
      :initial-user="balanceInitial"
      :allowed-types="[{ value: 'trader', label: 'Трейдер' }]"
      :lock-entity-type="!!balanceInitial"
      @update:open="showBalanceModal = $event"
      @success="load"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseSwitch from '@/components/ui/BaseSwitch.vue'
import BaseTabs from '@/components/ui/BaseTabs.vue'
import BaseTransferList from '@/components/ui/BaseTransferList.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import BalanceAdjustModal from '@/components/modals/BalanceAdjustModal.vue'
import { tradersService } from '@/api/services/traders.service'
import { usersService } from '@/api/services/users.service'
import { merchantsService } from '@/api/services/merchants.service'
import { financesService } from '@/api/services/finances.service'
import { teamleadsService } from '@/api/services/teamleads.service'
import { useToast } from '@/composables/useToast'
import type {
  Trader,
  User,
  TeamleadLink,
  BalanceInfo,
  PaymentMethod,
  MerchantAdmin,
  TraderGroup,
} from '@/types'
import { traderStatusOptions } from '@/constants'

const toast = useToast()
const ALL_METHODS: PaymentMethod[] = ['sbp', 'card', 'sim']

const loading = ref(false)
const saving = ref(false)
const searchQuery = ref('')
const traders = ref<Trader[]>([])
const usersMap = ref<Record<number, User>>({})
const balances = ref<BalanceInfo[]>([])
const teamleadLinks = ref<TeamleadLink[]>([])
const teamleadUsers = ref<Record<number, User>>({})
const page = ref(1)
const perPage = ref(25)
const totalPages = ref(1)

const showDetail = ref(false)
const detail = ref<Trader | null>(null)

const showBalanceModal = ref(false)
const balanceInitial = ref<{ id: number; username: string; role: 'trader' } | null>(null)

interface MethodEdit { fee: string; min_amount: string; max_amount: string; is_active: boolean }

interface AccessForm {
  status: string
  telegram_group_id: string
  accept_all_merchants: boolean
  merchant_ids: number[]
  group_ids: number[]
  priority_bonus_percent: string
}

const editForm = reactive<AccessForm>({
  status: '',
  telegram_group_id: '',
  accept_all_merchants: false,
  merchant_ids: [],
  group_ids: [],
  priority_bonus_percent: '0',
})
const methodsMap = reactive<Record<string, MethodEdit>>(
  ALL_METHODS.reduce((acc, m) => {
    acc[m] = { fee: '0', min_amount: '0', max_amount: '0', is_active: false }
    return acc
  }, {} as Record<string, MethodEdit>),
)

const activeTab = ref('profile')
const tabs = [
  { key: 'profile', label: 'Профиль' },
  { key: 'methods', label: 'Методы' },
  { key: 'access', label: 'Мерчанты и Группы' },
]

const allMerchants = ref<MerchantAdmin[]>([])
const allGroups = ref<TraderGroup[]>([])

const merchantOptions = computed(() =>
  allMerchants.value.map(m => ({
    value: m.id,
    label: m.name || `Мерчант #${m.id}`,
    sublabel: `#${m.id}`,
  })),
)

const groupOptions = computed(() =>
  allGroups.value.map(g => ({
    value: g.id,
    label: g.name,
    sublabel: g.description || undefined,
  })),
)

const columns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'username', label: 'Логин' },
  { key: 'payin_method', label: 'Payin' },
  { key: 'payout_display', label: 'Payout' },
  { key: 'fee_display', label: 'Финконфиг' },
  { key: 'teamlead_name', label: 'Тимлид' },
  { key: 'tl_fee', label: 'ФК тимлида' },
  { key: 'currency', label: 'Валюта' },
  { key: 'balance', label: 'Баланс' },
  { key: 'safe_deposit', label: 'Страх. деп' },
  { key: 'status', label: 'Статус' },
]

const statusOptions = traderStatusOptions

function toggleMethod(method: PaymentMethod) {
  methodsMap[method].is_active = !methodsMap[method].is_active
}

function getPayinMethod(t: Trader): string {
  const mc = t.methods_config || {}
  const keys = Object.keys(mc).filter(k => (mc as any)[k]?.is_active !== false)
  return keys.length ? keys.map(k => k.toUpperCase()).join(', ') : ''
}

function getFeeDisplay(t: Trader): string {
  const mc = t.methods_config || {}
  const entries = Object.entries(mc).filter(([, v]: [string, any]) => v?.is_active !== false)
  if (!entries.length) return '—'
  return entries.map(([k, v]: [string, any]) => `${k}: ${v.fee ?? 0}%`).join(', ')
}

const detailUsername = computed(() => {
  if (!detail.value) return ''
  return usersMap.value[detail.value.user_id]?.username ?? '—'
})

const detailPayinMethod = computed(() => detail.value ? getPayinMethod(detail.value) : '')

const rows = computed(() =>
  traders.value.map((t) => {
    const user = usersMap.value[t.user_id]
    const bal = balances.value.find(b => b.user_id === t.user_id && b.type === 'work')
    const safeDep = balances.value.find(b => b.user_id === t.user_id && b.type === 'safe_deposit')
    const link = teamleadLinks.value.find(l => l.linked_entity_type === 'trader' && l.linked_entity_id === t.id)
    const tlUser = link ? teamleadUsers.value[link.teamlead_id] : null

    return {
      ...t,
      username: user?.username ?? '—',
      payin_method: getPayinMethod(t),
      payout_display: t.is_payout_active,
      fee_display: getFeeDisplay(t),
      teamlead_name: tlUser?.username ?? (link ? `ID:${link.teamlead_id}` : '—'),
      tl_fee: link ? `${link.fee_percent}%` : '—',
      currency: 'RUB',
      balance: bal ? bal.amount : null,
      safe_deposit: safeDep ? safeDep.amount : null,
    }
  })
)

function applyFilters() {
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const params: any = { skip: (page.value - 1) * perPage.value, limit: perPage.value }
    if (searchQuery.value) params.search = searchQuery.value
    const { data } = await tradersService.list(params)
    traders.value = data
    totalPages.value = data.length < perPage.value ? page.value : page.value + 1

    const [usersRes, balRes, linksRes] = await Promise.all([
      usersService.list({ role: 'trader', limit: 500 }),
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
  } catch { toast.error('Ошибка загрузки трейдеров') }
  finally { loading.value = false }
}

async function loadAccessOptions() {
  try {
    const [merchantsRes, groupsRes] = await Promise.all([
      merchantsService.listAll({ limit: 1000 }),
      tradersService.listGroups(),
    ])
    allMerchants.value = merchantsRes.data
    allGroups.value = groupsRes.data
  } catch {
    // мягкая ошибка — модалка работает и без подгрузки опций (поля будут пустые)
  }
}

async function openDetail(row: any) {
  const t = traders.value.find(x => x.id === row.id)
  if (!t) return
  detail.value = t
  editForm.status = t.status
  editForm.telegram_group_id = t.telegram_group_id != null ? String(t.telegram_group_id) : ''
  editForm.accept_all_merchants = !!t.accept_all_merchants
  editForm.priority_bonus_percent = String(t.priority_bonus_percent ?? 0)
  editForm.merchant_ids = (t.merchants || []).map(m => m.id)
  editForm.group_ids = (t.groups || []).map(g => g.id)

  const mc = t.methods_config || {}
  for (const method of ALL_METHODS) {
    const cfg: any = (mc as any)[method]
    if (cfg) {
      methodsMap[method].fee = String(cfg.fee ?? 0)
      methodsMap[method].min_amount = String(cfg.min_amount ?? 0)
      methodsMap[method].max_amount = String(cfg.max_amount ?? 0)
      methodsMap[method].is_active = cfg.is_active !== false
    } else {
      methodsMap[method].fee = '0'
      methodsMap[method].min_amount = '0'
      methodsMap[method].max_amount = '0'
      methodsMap[method].is_active = false
    }
  }
  activeTab.value = 'profile'
  showDetail.value = true

  // Re-fetch latest trader (to get accept_all_merchants/merchants/groups even if list endpoint hasn't returned them)
  try {
    const { data: fresh } = await tradersService.getById(t.id)
    detail.value = fresh
    editForm.accept_all_merchants = !!fresh.accept_all_merchants
    editForm.merchant_ids = (fresh.merchants || []).map(m => m.id)
    editForm.group_ids = (fresh.groups || []).map(g => g.id)
  } catch {
    // если endpoint недоступен — остаёмся с данными из листа
  }
}

function openBalanceModal() {
  balanceInitial.value = null
  showBalanceModal.value = true
}

function openDetailBalance() {
  if (!detail.value) return
  const user = usersMap.value[detail.value.user_id]
  if (!user) {
    toast.error('Не удалось определить пользователя')
    return
  }
  balanceInitial.value = { id: user.id, username: user.username, role: 'trader' }
  showBalanceModal.value = true
}

async function saveTrader() {
  if (!detail.value) return
  saving.value = true
  try {
    const methods_config: Record<string, { fee: number; min_amount: number; max_amount: number; is_active: boolean }> = {}
    for (const method of ALL_METHODS) {
      const m = methodsMap[method]
      methods_config[method] = {
        fee: Number(m.fee) || 0,
        min_amount: Number(m.min_amount) || 0,
        max_amount: Number(m.max_amount) || 0,
        is_active: m.is_active,
      }
    }

    const groupId = editForm.telegram_group_id.trim()
    await tradersService.update(detail.value.id, {
      status: editForm.status as any,
      is_payin_active: detail.value.is_payin_active,
      is_payout_active: detail.value.is_payout_active,
      telegram_group_id: groupId ? Number(groupId) : null,
      methods_config,
      accept_all_merchants: editForm.accept_all_merchants,
      merchant_ids: editForm.merchant_ids,
      group_ids: editForm.group_ids,
      priority_bonus_percent: Number(editForm.priority_bonus_percent) || 0,
    })
    toast.success('Трейдер обновлён')
    showDetail.value = false
    load()
  } catch { toast.error('Ошибка обновления') }
  finally { saving.value = false }
}

onMounted(() => {
  load()
  loadAccessOptions()
})
</script>
