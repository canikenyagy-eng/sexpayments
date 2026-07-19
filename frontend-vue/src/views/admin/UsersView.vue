<template>
  <div>
    <PageHeader title="Пользователи">
      <template #actions>
        <BaseButton variant="gold" @click="showCreateModal = true">+ Создать</BaseButton>
      </template>
    </PageHeader>

    <div class="mb-4 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
      <BaseInput v-model="filters.search" label="Поиск" placeholder="ID или логин" />
      <BaseSelect
        v-model="filters.role"
        label="Роль"
        :options="[
          { value: '', label: 'Все' },
          { value: 'admin', label: 'Админ' },
          { value: 'support', label: 'Саппорт' },
          { value: 'merchant', label: 'Мерчант' },
          { value: 'trader', label: 'Трейдер' },
          { value: 'teamlead', label: 'Тимлид' },
        ]"
      />
      <BaseSelect
        v-model="filters.is_active"
        label="Статус"
        :options="[
          { value: '', label: 'Все' },
          { value: 'true', label: 'Активен' },
          { value: 'false', label: 'Заблокирован' },
        ]"
      />
      <div class="grid grid-cols-2 gap-2">
        <BaseInput v-model="filters.balance_from" label="Баланс от (USDT)" type="number" placeholder="0" />
        <BaseInput v-model="filters.balance_to" label="Баланс до (USDT)" type="number" placeholder="∞" />
      </div>
      <div class="flex items-end gap-2 lg:col-span-4">
        <BaseButton variant="gold" size="sm" @click="applyFilters">Применить</BaseButton>
        <BaseButton variant="dark" size="sm" @click="resetFilters">Сброс</BaseButton>
      </div>
    </div>

    <DataTable
      :columns="columns"
      :rows="enrichedRows"
      :loading="loading"
      row-key="id"
      :current-page="page"
      :total-pages="totalPages"
      @page-change="p => { page = p; loadUsers() }"
    >
      <template #cell-role="{ value }">
        <RoleBadge :role="value" />
      </template>
      <template #cell-is_blocked="{ value }">
        <StatusBadge :status="!value ? 'active' : 'blocked'" />
      </template>
      <template #cell-balance="{ value }">
        <span v-if="value !== null" class="font-mono text-xs">{{ value }}</span>
        <span v-else class="text-text-muted">—</span>
      </template>
      <template #cell-fee_display="{ value }">
        <span v-if="value" class="text-xs">{{ value }}</span>
        <span v-else class="text-text-muted">—</span>
      </template>
      <template #cell-created_at="{ value }">
        {{ new Date(value).toLocaleDateString('ru-RU') }}
      </template>
      <template #actions="{ row }">
        <div class="flex gap-1">
          <BaseButton action="edit" variant="ghost" size="sm" @click="openEdit(row as any)" />
          <BaseButton v-if="row.totp_enabled" variant="ghost" size="sm" title="Сбросить 2FA" @click="reset2FA(row as any)">2FA</BaseButton>
          <BaseButton action="impersonate" variant="ghost" size="sm" @click="impersonate(row as any)" />
        </div>
      </template>
    </DataTable>

    <!-- Edit modal -->
    <BaseModal v-model="showEditModal" title="Редактирование пользователя">
      <div v-if="editingUser" class="space-y-4">
        <p class="text-sm text-text-muted">Пользователь: <strong class="text-text-main">{{ editingUser.username }}</strong> (ID: {{ editingUser.id }})</p>
        <BaseButton
          :variant="editForm.is_blocked === 'true' ? 'gold' : 'danger'"
          class="w-full"
          @click="editForm.is_blocked = editForm.is_blocked === 'true' ? 'false' : 'true'"
        >
          {{ editForm.is_blocked === 'true' ? 'Разблокировать' : 'Заблокировать' }}
        </BaseButton>
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showEditModal = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="saving" @click="saveEdit">Сохранить</BaseButton>
      </template>
    </BaseModal>

    <!-- Create modal -->
    <CreateUserModal v-model="showCreateModal" @created="loadUsers" />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import RoleBadge from '@/components/ui/RoleBadge.vue'
import CreateUserModal from '@/components/modals/CreateUserModal.vue'
import { usersService } from '@/api/services/users.service'
import { financesService } from '@/api/services/finances.service'
import { teamleadsService } from '@/api/services/teamleads.service'
import { merchantsService } from '@/api/services/merchants.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useAuthStore } from '@/stores/auth'
import { useRouter } from 'vue-router'
import type { User, TeamleadLink, BalanceInfo } from '@/types'

const toast = useToast()
const { confirm } = useConfirm()
const authStore = useAuthStore()
const router = useRouter()
const PER_PAGE = 25

const loading = ref(false)
const saving = ref(false)
const users = ref<User[]>([])
const balances = ref<BalanceInfo[]>([])
const teamleadLinks = ref<TeamleadLink[]>([])
const terminalsByOwner = ref<Record<number, number[]>>({})
const page = ref(1)
const totalPages = ref(1)
const filters = reactive({
  role: '',
  search: '',
  is_active: '' as '' | 'true' | 'false',
  balance_from: '',
  balance_to: '',
})

const showEditModal = ref(false)
const showCreateModal = ref(false)
const editingUser = ref<User | null>(null)
const editForm = reactive({ is_blocked: 'false' })

const columns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'username', label: 'Логин' },
  { key: 'role', label: 'Роль' },
  { key: 'balance', label: 'Баланс' },
  { key: 'fee_display', label: 'Финконфиг' },
  { key: 'is_blocked', label: 'Статус' },
  { key: 'created_at', label: 'Создан' },
]

const enrichedRows = computed(() =>
  users.value.map((u) => {
    const hasFeeRole = u.role === 'merchant' || u.role === 'trader'
    const link = teamleadLinks.value.find(l =>
      (l.linked_entity_type === u.role) && (l.linked_entity_id === u.id)
    )

    let resolvedBalance: number | null
    if (u.role === 'merchant') {
      const terminalIds = terminalsByOwner.value[u.id] ?? []
      const terminalTotal = balances.value
        .filter(b =>
          b.merchant_id != null &&
          terminalIds.includes(b.merchant_id) &&
          b.type === 'work' &&
          b.currency === 'USDT',
        )
        .reduce((acc, b) => acc + Number(b.amount || 0), 0)
      const ownerTotal = balances.value
        .filter(b =>
          b.user_id === u.id &&
          b.type === 'work' &&
          b.currency === 'USDT',
        )
        .reduce((acc, b) => acc + Number(b.amount || 0), 0)
      resolvedBalance = terminalTotal + ownerTotal
    } else {
      const userBalance = balances.value.find(b =>
        b.user_id === u.id && b.type === 'work' && b.currency === 'USDT',
      )
      resolvedBalance = u.balance_usdt ?? (userBalance ? userBalance.amount : null)
    }

    return {
      ...u,
      balance: resolvedBalance,
      fee_display: hasFeeRole && link ? `${link.fee_percent}%` : '',
    }
  })
)

function applyFilters() {
  page.value = 1
  loadUsers()
}

function resetFilters() {
  filters.role = ''
  filters.search = ''
  filters.is_active = ''
  filters.balance_from = ''
  filters.balance_to = ''
  page.value = 1
  loadUsers()
}

async function loadUsers() {
  loading.value = true
  try {
    const params: any = { skip: (page.value - 1) * PER_PAGE, limit: PER_PAGE }
    if (filters.role) params.role = filters.role
    if (filters.search) params.search = filters.search
    if (filters.is_active) params.is_active = filters.is_active === 'true'
    if (filters.balance_from) params.balance_from = Number(filters.balance_from)
    if (filters.balance_to) params.balance_to = Number(filters.balance_to)

    const { data } = await usersService.list(params)
    users.value = data
    totalPages.value = data.length < PER_PAGE ? page.value : page.value + 1

    const [balRes, linksRes, merchRes] = await Promise.all([
      financesService.listBalances({ limit: 2000 }),
      teamleadsService.listAllLinks({ limit: 500 }),
      merchantsService.listAll({ limit: 2000 }),
    ])
    balances.value = balRes.data
    teamleadLinks.value = linksRes.data

    const ownerMap: Record<number, number[]> = {}
    for (const m of merchRes.data) {
      if (!ownerMap[m.user_id]) ownerMap[m.user_id] = []
      ownerMap[m.user_id].push(m.id)
    }
    terminalsByOwner.value = ownerMap
  } catch { toast.error('Ошибка загрузки пользователей') }
  finally { loading.value = false }
}

function openEdit(user: User) {
  editingUser.value = user
  editForm.is_blocked = String(user.is_blocked)
  showEditModal.value = true
}

async function saveEdit() {
  if (!editingUser.value) return
  saving.value = true
  try {
    await usersService.update(editingUser.value.id, {
      is_blocked: editForm.is_blocked === 'true',
    })
    toast.success('Пользователь обновлён')
    showEditModal.value = false
    loadUsers()
  } catch { toast.error('Ошибка сохранения') }
  finally { saving.value = false }
}

async function reset2FA(user: User) {
  if (!(await confirm(`Сбросить 2FA для ${user.username}?`))) return
  try {
    await usersService.reset2FA(user.id)
    toast.success('2FA сброшен')
    loadUsers()
  } catch { toast.error('Ошибка сброса 2FA') }
}

async function impersonate(user: User) {
  if (!(await confirm(`Войти в систему как ${user.username}?`))) return
  try {
    await authStore.impersonate(user.id)
    toast.success(`Вы вошли как ${user.username}`)
    router.push('/')
  } catch (e: any) {
    const msg = e?.response?.data?.error_message || 'Ошибка входа'
    toast.error(msg)
  }
}

onMounted(loadUsers)
</script>
