<template>
  <div>
    <PageHeader title="Выводы" />

    <div class="mb-4 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
      <BaseSelect
        v-model="filters.status"
        label="Статус"
        :options="[
          { value: '', label: 'Все' },
          { value: 'pending', label: 'Ожидание' },
          { value: 'success', label: 'Успешно' },
          { value: 'rejected', label: 'Отклонён' },
        ]"
      />
      <BaseSelect
        v-model="filters.user_role"
        label="Тип пользователя"
        :options="[
          { value: '', label: 'Все' },
          { value: 'merchant', label: 'Мерчант' },
          { value: 'trader', label: 'Трейдер' },
          { value: 'teamlead', label: 'Тимлид' },
        ]"
      />
      <BaseInput v-model="filters.user_login" label="Логин пользователя" placeholder="login" />
      <div class="flex items-end gap-2 lg:col-span-4">
        <BaseButton variant="gold" size="sm" @click="applyFilters">Применить</BaseButton>
        <BaseButton variant="dark" size="sm" @click="resetFilters">Сброс</BaseButton>
      </div>
    </div>

    <DataTable
      :columns="columns"
      :rows="items"
      :loading="loading"
      row-key="id"
      :current-page="page"
      :total-pages="totalPages"
      :per-page="perPage"
      @page-change="p => { page = p; load() }"
      @per-page-change="n => { perPage = n; page = 1; load() }"
    >
      <template #cell-user_display="{ row }">
        <span class="text-xs">
          <strong class="text-text-main">{{ row.user_login ?? `#${row.user_id}` }}</strong>
        </span>
      </template>
      <template #cell-amount="{ row }">
        {{ row.amount }} {{ row.currency }}
      </template>
      <template #cell-fee_amount="{ value }">
        {{ value }}
      </template>
      <template #cell-status="{ value }">
        <StatusBadge :status="value" />
      </template>
      <template #cell-user_role="{ value }">
        <BaseBadge color="gold">{{ value }}</BaseBadge>
      </template>
      <template #cell-created_at="{ value }">
        {{ formatDate(value) }}
      </template>
      <template #actions="{ row }">
        <div v-if="row.status === 'pending'" class="flex gap-1">
          <BaseButton action="accept" variant="success" size="sm" :loading="acting" @click="approve(row as WithdrawalRequest)" />
          <BaseButton action="cancel" variant="danger" size="sm" @click="openReject(row as WithdrawalRequest)" />
        </div>
      </template>
    </DataTable>

    <BaseModal v-model="showReject" title="Отклонить вывод">
      <BaseInput v-model="rejectReason" label="Причина отклонения" required />
      <template #footer>
        <BaseButton variant="dark" @click="showReject = false">Отмена</BaseButton>
        <BaseButton variant="danger" :loading="acting" @click="rejectWithdrawal">Отклонить</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { financesService } from '@/api/services/finances.service'
import { useToast } from '@/composables/useToast'
import { formatDate } from '@/utils/format'
import type { WithdrawalRequest, WithdrawalStatus, UserRole } from '@/types'

const toast = useToast()

const loading = ref(false)
const acting = ref(false)
const items = ref<WithdrawalRequest[]>([])
const page = ref(1)
const perPage = ref(25)
const totalPages = ref(1)

const filters = reactive({
  status: '' as '' | WithdrawalStatus,
  user_role: '' as '' | UserRole,
  user_login: '',
})

const showReject = ref(false)
const rejectReason = ref('')
const rejectTarget = ref<WithdrawalRequest | null>(null)

const columns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'user_display', label: 'Пользователь' },
  { key: 'user_role', label: 'Роль' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'fee_amount', label: 'Fee', align: 'right' },
  { key: 'destination_address', label: 'Адрес' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Дата' },
]

function applyFilters() {
  page.value = 1
  load()
}

function resetFilters() {
  filters.status = ''
  filters.user_role = ''
  filters.user_login = ''
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const params: any = { skip: (page.value - 1) * perPage.value, limit: perPage.value }
    if (filters.status) params.status = filters.status
    if (filters.user_role) params.user_role = filters.user_role
    if (filters.user_login) params.user_login = filters.user_login
    const { data } = await financesService.listWithdrawals(params)
    items.value = data
    totalPages.value = data.length < perPage.value ? page.value : page.value + 1
  } catch { toast.error('Ошибка загрузки') }
  finally { loading.value = false }
}

async function approve(row: WithdrawalRequest) {
  acting.value = true
  try {
    await financesService.approveWithdrawal(row.id)
    toast.success('Вывод одобрен')
    load()
  } catch { toast.error('Ошибка') }
  finally { acting.value = false }
}

function openReject(row: WithdrawalRequest) {
  rejectTarget.value = row
  rejectReason.value = ''
  showReject.value = true
}

async function rejectWithdrawal() {
  if (!rejectTarget.value || !rejectReason.value) return
  acting.value = true
  try {
    await financesService.rejectWithdrawal(rejectTarget.value.id, rejectReason.value)
    toast.success('Вывод отклонён')
    showReject.value = false
    load()
  } catch { toast.error('Ошибка') }
  finally { acting.value = false }
}

onMounted(load)
</script>
