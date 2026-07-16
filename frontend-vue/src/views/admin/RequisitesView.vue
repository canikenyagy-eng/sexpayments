<template>
  <div>
    <PageHeader title="Реквизиты" />

    <div class="mb-4 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
      <BaseInput v-model="filters.trader_login" label="Логин трейдера" placeholder="trader" />
      <BaseSelect v-model="filters.payment_method" label="Метод" :options="methodOptions" />
      <BaseInput v-model="filters.bank" label="Банк / пеймент-опция" placeholder="банк" />
      <BaseSelect v-model="filters.is_enabled" label="Включён" :options="tristateOptions" />
      <BaseSelect v-model="filters.is_active" label="Активен" :options="tristateOptions" />
      <div class="flex items-end gap-2 lg:col-span-2">
        <BaseButton variant="gold" size="sm" @click="applyFilters">Применить</BaseButton>
        <BaseButton variant="dark" size="sm" @click="resetFilters">Сброс</BaseButton>
      </div>
    </div>

    <DataTable
      :columns="columns"
      :rows="items"
      :loading="loading"
      row-key="id"
      clickable
      :current-page="page"
      :total-pages="totalPages"
      :per-page="perPage"
      @page-change="p => { page = p; load() }"
      @per-page-change="onPerPageChange"
      @row-click="openInfo($event as any)"
    >
      <template #cell-status="{ row }">
        <StatusBadge :status="(row as Requisite).is_archived ? 'archived' : (row as Requisite).status" />
      </template>
      <template #cell-is_active="{ value }">
        <StatusBadge :status="String(value)" />
      </template>
      <template #cell-bank_name="{ row }">
        <div class="flex items-center gap-2">
          <PaymentOptionLogo
            :src="(row as Requisite).payment_option?.logo_url ?? null"
            :alt="(row as Requisite).payment_option?.name ?? (row as Requisite).bank_name"
            :size="20"
          />
          <span class="truncate text-text-main">
            {{ (row as Requisite).payment_option?.name || (row as Requisite).bank_name }}
          </span>
        </div>
      </template>
      <template #cell-account_number="{ row }">
        <button
          type="button"
          class="group flex items-center rounded px-1 py-0.5 transition-colors hover:bg-bg-hover active:bg-bg-card"
          title="Скопировать"
          @click.stop="copyAccount((row as Requisite).account_number)"
        >
          <span class="font-mono text-xs text-text-main transition-colors group-hover:text-accent">
            {{ (row as Requisite).account_number }}
          </span>
        </button>
      </template>
      <template #cell-payment_method="{ value }">
        <MethodBadge :method="value" />
      </template>
      <template #cell-limits="{ value }">
        <div @click.stop>
          <RequisiteLimits :limits="value" />
        </div>
      </template>
    </DataTable>

    <!-- Requisite detail modal: see AdminRequisiteDetailModal for layout. -->
    <AdminRequisiteDetailModal
      v-model="showInfo"
      :requisite="selected"
      @edit="selected && openEdit(selected)"
      @delete="selected && removeReq(selected)"
    />

    <BaseModal v-model="showEdit" title="Редактирование реквизита">
      <div v-if="editing" class="space-y-4">
        <BaseSelect
          v-model="editForm.status"
          label="Статус"
          :options="[
            { value: 'enabled', label: 'Включён' },
            { value: 'disabled', label: 'Выключен' },
            { value: 'blocked', label: 'Заблокирован' },
            { value: 'archived', label: 'Архив' },
          ]"
        />
        <BaseSelect
          v-model="editForm.is_active"
          label="Активен"
          :options="[{ value: 'true', label: 'Да' }, { value: 'false', label: 'Нет' }]"
        />
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showEdit = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="saving" @click="saveEdit">Сохранить</BaseButton>
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
import MethodBadge from '@/components/ui/MethodBadge.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import RequisiteLimits from '@/components/ui/RequisiteLimits.vue'
import PaymentOptionLogo from '@/components/ui/PaymentOptionLogo.vue'
import AdminRequisiteDetailModal from '@/components/modals/AdminRequisiteDetailModal.vue'
import { requisitesService } from '@/api/services/requisites.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { copyToClipboard } from '@/utils/format'
import type { PaymentMethod, Requisite, RequisiteStatus } from '@/types'
import { paymentMethodOptionsWithAll, tristateOptions } from '@/constants'

const toast = useToast()
const { confirm } = useConfirm()

const loading = ref(false)
const saving = ref(false)
const rawItems = ref<Requisite[]>([])
const page = ref(1)
const perPage = ref(25)
const totalPages = ref(1)

const filters = reactive({
  trader_login: '',
  payment_method: '' as '' | PaymentMethod,
  bank: '',
  is_enabled: '' as '' | 'true' | 'false',
  is_active: '' as '' | 'true' | 'false',
})

const methodOptions = paymentMethodOptionsWithAll

const showEdit = ref(false)
const editing = ref<Requisite | null>(null)
const editForm = reactive({ status: '', is_active: 'true' })

const showInfo = ref(false)
const selected = ref<Requisite | null>(null)

const columns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'trader_name', label: 'Трейдер' },
  { key: 'bank_name', label: 'Банк' },
  { key: 'account_number', label: 'Реквизит' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'status', label: 'Статус' },
  { key: 'is_active', label: 'Активен' },
  { key: 'limits', label: 'Лимит (дн.)', cellClass: 'py-1' },
]

const items = computed(() =>
  rawItems.value.map(r => ({
    ...r,
    trader_name: r.trader_login ?? `#${r.trader_id}`,
  }))
)

function applyFilters() {
  page.value = 1
  load()
}

function resetFilters() {
  filters.trader_login = ''
  filters.payment_method = ''
  filters.bank = ''
  filters.is_enabled = ''
  filters.is_active = ''
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
    const params: any = {
      skip: (page.value - 1) * perPage.value,
      limit: perPage.value,
    }
    if (filters.trader_login) params.trader_login = filters.trader_login.trim()
    if (filters.payment_method) params.payment_method = filters.payment_method
    if (filters.bank) params.bank = filters.bank.trim()
    if (filters.is_enabled) params.is_enabled = filters.is_enabled === 'true'
    if (filters.is_active) params.is_active = filters.is_active === 'true'

    const { data } = await requisitesService.listAll(params)
    rawItems.value = data
    totalPages.value = data.length < perPage.value ? page.value : page.value + 1
  } catch { toast.error('Ошибка загрузки') }
  finally { loading.value = false }
}

function openInfo(row: Requisite) {
  selected.value = row
  showInfo.value = true
}

function openEdit(row: Requisite) {
  editing.value = row
  editForm.status = row.status
  editForm.is_active = String(row.is_active)
  showInfo.value = false
  showEdit.value = true
}

async function saveEdit() {
  if (!editing.value) return
  saving.value = true
  try {
    await requisitesService.update(editing.value.id, {
      status: editForm.status as RequisiteStatus,
      is_active: editForm.is_active === 'true',
    })
    toast.success('Реквизит обновлён')
    showEdit.value = false
    load()
  } catch { toast.error('Ошибка') }
  finally { saving.value = false }
}

async function copyAccount(text: string) {
  try {
    await copyToClipboard(text)
    toast.success('Скопировано')
  } catch {
    toast.error('Не удалось скопировать')
  }
}

async function removeReq(row: Requisite) {
  if (!(await confirm(`Удалить реквизит #${row.id}?`))) return
  try {
    await requisitesService.remove(row.id)
    toast.success('Реквизит удалён')
    showInfo.value = false
    load()
  } catch { toast.error('Ошибка удаления') }
}

onMounted(() => {
  load()
})
</script>
