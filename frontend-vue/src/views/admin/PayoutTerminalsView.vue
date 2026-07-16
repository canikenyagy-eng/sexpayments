<template>
  <div>
    <PageHeader title="Выплатные терминалы">
      <template #actions>
        <BaseButton variant="gold" @click="openCreate">+ Создать терминал</BaseButton>
      </template>
    </PageHeader>

    <div class="mb-4 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
      <BaseInput v-model="filters.search" label="Поиск" placeholder="ID или название" />
      <BaseSelect v-model="filters.status" label="Статус" :options="statusFilterOptions" />
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
      @page-change="p => page = p"
      @per-page-change="n => { perPage = n; page = 1 }"
    >
      <template #cell-status="{ value }">
        <StatusBadge :status="value" context="merchant" />
      </template>
      <template #cell-commission_display="{ value }"><span class="text-xs">{{ value }}</span></template>
      <template #cell-work_usdt="{ value }"><span class="font-mono text-xs text-status-success">{{ formatAmount(value) }}</span></template>
      <template #cell-escrow_usdt="{ value }"><span class="font-mono text-xs text-accent">{{ formatAmount(value) }}</span></template>
    </DataTable>

    <!-- Create -->
    <BaseModal v-model="showCreate" title="Новый выплатной терминал">
      <div class="space-y-3">
        <BaseInput v-model="form.name" label="Название" required />
        <BaseInput v-model="form.owner_user_id" label="ID владельца (user)" type="number" required />
        <BaseInput v-model="form.commission_percent" label="Комиссия %" type="number" />
        <BaseInput v-model="form.ttl_minutes" label="TTL (минут)" type="number" />
        <BaseInput v-model="form.receipts_to_close" label="Чеков для закрытия" type="number" />
        <BaseInput v-model="form.rate_config_id" label="ID курса (rate config)" type="number" />
        <BaseInput v-model="form.trader_ids" label="ID трейдеров (через запятую)" />
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showCreate = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="saving" @click="create">Создать</BaseButton>
      </template>
    </BaseModal>

    <!-- Key reveal after create -->
    <BaseModal v-model="showKeyModal" title="Ключи терминала">
      <div class="space-y-3">
        <p class="text-sm text-status-warning">Сохраните ключ и секрет — секрет больше не будет показан!</p>
        <div>
          <p class="mb-1 text-xs font-bold text-text-muted">API Key</p>
          <div class="rounded-xl bg-bg-card p-3 font-mono text-sm text-accent break-all">{{ keyInfo.api_key }}</div>
        </div>
        <div v-if="keyInfo.api_secret">
          <p class="mb-1 text-xs font-bold text-text-muted">API Secret</p>
          <div class="rounded-xl bg-bg-card p-3 font-mono text-sm text-accent break-all">{{ keyInfo.api_secret }}</div>
        </div>
      </div>
      <template #footer><BaseButton variant="gold" @click="showKeyModal = false">Закрыть</BaseButton></template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, watch, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { payoutsService } from '@/api/services/payouts.service'
import { useToast } from '@/composables/useToast'
import { formatAmount } from '@/utils/format'
import { merchantStatusOptionsWithAll } from '@/constants'
import type { PayoutTerminal } from '@/types'

const toast = useToast()
const router = useRouter()

const loading = ref(false)
const saving = ref(false)
const allTerminals = ref<PayoutTerminal[]>([])
const page = ref(1)
const perPage = ref(25)

const filters = reactive({ search: '', status: '' })
const statusFilterOptions = merchantStatusOptionsWithAll

const columns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'name', label: 'Название' },
  { key: 'currency', label: 'Валюта' },
  { key: 'commission_display', label: 'Комиссия' },
  { key: 'ttl_minutes', label: 'TTL мин' },
  { key: 'receipts_to_close', label: 'Чеков' },
  { key: 'work_usdt', label: 'WORK' },
  { key: 'escrow_usdt', label: 'ESCROW' },
  { key: 'status', label: 'Статус' },
]

const filtered = computed(() =>
  allTerminals.value.filter(t => {
    if (filters.status && t.status !== filters.status) return false
    if (filters.search) {
      const q = filters.search.toLowerCase()
      if (!String(t.id).includes(q) && !(t.name || '').toLowerCase().includes(q)) return false
    }
    return true
  })
)
const totalPages = computed(() => Math.max(1, Math.ceil(filtered.value.length / perPage.value)))

// Filtering shrinks the list — jump back to page 1 so we never sit on an empty page.
watch(() => [filters.search, filters.status], () => { page.value = 1 })
const rows = computed(() => {
  const start = (page.value - 1) * perPage.value
  return filtered.value.slice(start, start + perPage.value).map(t => ({
    ...t,
    commission_display: `${t.commission_percent}%`,
  }))
})

const showCreate = ref(false)
const form = reactive({
  name: '', owner_user_id: '', commission_percent: '0', ttl_minutes: '60',
  receipts_to_close: '1', rate_config_id: '', trader_ids: '',
})
const showKeyModal = ref(false)
const keyInfo = reactive<{ api_key: string; api_secret?: string }>({ api_key: '' })

async function load() {
  loading.value = true
  try { allTerminals.value = (await payoutsService.listTerminals({ limit: 500 })).data }
  catch { toast.error('Ошибка загрузки терминалов') }
  finally { loading.value = false }
}

function goToDetail(row: any) {
  router.push({ name: 'admin-payout-terminal-detail', params: { id: String(row.id) } })
}

function openCreate() { showCreate.value = true }

async function create() {
  saving.value = true
  try {
    const traderIds = form.trader_ids.split(',').map(s => Number(s.trim())).filter(n => !isNaN(n) && n > 0)
    const { data } = await payoutsService.createTerminal({
      name: form.name,
      owner_user_id: Number(form.owner_user_id),
      commission_percent: Number(form.commission_percent),
      ttl_minutes: Number(form.ttl_minutes),
      receipts_to_close: Number(form.receipts_to_close),
      rate_config_id: form.rate_config_id ? Number(form.rate_config_id) : null,
      trader_ids: traderIds,
    })
    toast.success('Терминал создан')
    showCreate.value = false
    keyInfo.api_key = data.api_key
    keyInfo.api_secret = data.api_secret
    showKeyModal.value = true
    load()
  } catch (e: any) {
    toast.error(e.response?.data?.detail || 'Ошибка создания')
  } finally { saving.value = false }
}

onMounted(load)
</script>
