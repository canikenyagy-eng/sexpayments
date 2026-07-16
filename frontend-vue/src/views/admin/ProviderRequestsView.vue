<template>
  <div>
    <PageHeader title="Запросы провайдеров" />

    <div class="mb-4 flex items-center gap-2">
      <BaseFilter :active-count="filterCount" @apply="page = 1; load()" @reset="resetFilters">
        <ProviderSelect v-model="filters.provider_code" label="Провайдер" />
        <BaseSelect v-model="filters.success" label="Результат" :options="successOptions" />
        <BaseInput v-model="filters.order_id" label="Order ID" placeholder="123" />
        <BaseInput v-model="filters.request_id" label="Request ID" placeholder="uuid…" />
      </BaseFilter>
    </div>

    <div class="mb-4 flex flex-wrap gap-1 border-b border-border">
      <button
        v-for="t in typeTabs"
        :key="t.value"
        type="button"
        :class="[
          '-mb-px rounded-t-lg border-b-2 px-3 py-2 text-sm font-bold transition',
          activeType === t.value
            ? 'border-accent text-accent'
            : 'border-transparent text-text-muted hover:text-text-main',
        ]"
        @click="setType(t.value)"
      >
        {{ t.label }}
      </button>
    </div>

    <DataTable
      :columns="columns"
      :rows="rows"
      :loading="loading"
      row-key="_key"
      :current-page="page"
      :total-pages="totalPages"
      :per-page="perPage"
      clickable
      @row-click="row => openDetail(row as ProviderRequestLog)"
      @page-change="p => { page = p; load() }"
      @per-page-change="n => { perPage = n; page = 1; load() }"
    >
      <template #cell-provider_code="{ value }">
        <CascadeAdapterBadge
          v-if="value"
          :adapter="adapterForProviderCode(String(value))"
          :text="String(value)"
        />
        <span v-else class="text-text-muted">—</span>
      </template>
      <template #cell-request_type="{ value }">
        <BaseBadge :color="typeBadgeColor(String(value))">{{ value || 'other' }}</BaseBadge>
      </template>
      <template #cell-method="{ value }">
        <span class="font-mono text-xs">{{ value }}</span>
      </template>
      <template #cell-url="{ value }">
        <span class="block max-w-[320px] truncate font-mono text-xs" :title="value">{{ value }}</span>
      </template>
      <template #cell-response_status="{ value }">
        <span :class="['font-mono text-xs font-bold', statusClass(value as number)]">
          {{ value || '—' }}
        </span>
      </template>
      <template #cell-success="{ value }">
        <BaseBadge :color="value ? 'success' : 'danger'">{{ value ? 'OK' : 'Ошибка' }}</BaseBadge>
      </template>
      <template #cell-provider_latency_ms="{ value }">
        <span class="font-mono text-xs">{{ value }} мс</span>
      </template>
      <template #cell-e2e_ms="{ value }">
        <span class="font-mono text-xs">{{ value ? `${value} мс` : '—' }}</span>
      </template>
      <template #cell-ts="{ value }">
        {{ formatDate(value) }}
      </template>
    </DataTable>

    <BaseModal v-model="showDetail" :title="detailTitle" size="lg">
      <div v-if="detail" class="space-y-5">
        <div class="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span class="text-text-muted">Провайдер:</span>
            <span class="ml-1 font-mono text-text-main">
              {{ detail.provider_code }}<span v-if="detail.provider_id"> #{{ detail.provider_id }}</span>
            </span>
          </div>
          <div>
            <span class="text-text-muted">Результат:</span>
            <BaseBadge :color="detail.success ? 'success' : 'danger'" class="ml-1">
              {{ detail.success ? 'OK' : 'Ошибка' }}
            </BaseBadge>
          </div>
          <div>
            <span class="text-text-muted">Метод:</span>
            <span class="ml-1 font-mono text-text-main">{{ detail.method }}</span>
          </div>
          <div>
            <span class="text-text-muted">HTTP код:</span>
            <span :class="['ml-1 font-mono font-bold', statusClass(detail.response_status)]">
              {{ detail.response_status || '—' }}
            </span>
          </div>
          <div>
            <span class="text-text-muted">Провайдер:</span>
            <span class="ml-1 font-mono text-text-main">{{ detail.provider_latency_ms }} мс</span>
          </div>
          <div>
            <span class="text-text-muted">Всего (мерч→ответ):</span>
            <span class="ml-1 font-mono text-text-main">{{ detail.e2e_ms ? `${detail.e2e_ms} мс` : '—' }}</span>
          </div>
          <div>
            <span class="text-text-muted">Order ID:</span>
            <span class="ml-1 font-mono text-text-main">{{ detail.order_id || '—' }}</span>
          </div>
          <div>
            <span class="text-text-muted">Request ID:</span>
            <span class="ml-1 break-all font-mono text-xs text-text-main">{{ detail.request_id || '—' }}</span>
          </div>
          <div class="col-span-2">
            <span class="text-text-muted">Дата:</span>
            <span class="ml-1 text-text-main">{{ formatDate(detail.ts) }}</span>
          </div>
          <div class="col-span-2">
            <span class="text-text-muted">URL:</span>
            <span class="ml-1 break-all font-mono text-xs text-text-main">{{ detail.url }}</span>
          </div>
        </div>

        <div v-if="detail.error">
          <h4 class="mb-1 text-sm font-bold text-status-danger">Ошибка</h4>
          <pre class="overflow-x-auto rounded-lg bg-status-danger/10 p-3 text-xs text-status-danger">{{ detail.error }}</pre>
        </div>

        <div>
          <h4 class="mb-1 text-sm font-bold text-accent">Request Headers</h4>
          <pre class="overflow-x-auto rounded-lg bg-bg-card p-3 text-xs text-text-secondary" v-html="formatBody(detail.request_headers)"></pre>
        </div>

        <div>
          <h4 class="mb-1 text-sm font-bold text-accent">Request Body</h4>
          <pre class="overflow-x-auto rounded-lg bg-bg-card p-3 text-xs text-text-secondary" v-html="formatBody(detail.request_body)"></pre>
        </div>

        <div>
          <h4 class="mb-1 text-sm font-bold text-accent">Response Body</h4>
          <pre class="overflow-x-auto rounded-lg bg-bg-card p-3 text-xs text-text-secondary" v-html="formatBody(detail.response_body)"></pre>
        </div>
      </div>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import ProviderSelect from '@/components/ui/ProviderSelect.vue'
import BaseFilter from '@/components/ui/BaseFilter.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import CascadeAdapterBadge from '@/components/cascade/CascadeAdapterBadge.vue'
import { cascadeService } from '@/api/services/cascade.service'
import { useToast } from '@/composables/useToast'
import { formatBody, formatDate } from '@/utils/format'
import type { CascadeAdapterInfo, CascadeProvider, ProviderRequestLog } from '@/types'

const toast = useToast()

const loading = ref(false)
const items = ref<ProviderRequestLog[]>([])
const page = ref(1)
const perPage = ref(25)
const totalPages = ref(1)

// Catalog used only to put the integration logo on the provider-code badge:
// a request row carries `provider_code` but the logo lives on the adapter, so
// we map provider_code → provider.adapter_type → adapter (which has the logo).
const adapters = ref<CascadeAdapterInfo[]>([])
const providers = ref<CascadeProvider[]>([])

function adapterForProviderCode(code: string): CascadeAdapterInfo | null {
  const provider = providers.value.find((p) => p.code === code)
  if (!provider) return null
  return adapters.value.find((a) => a.code === provider.adapter_type) ?? null
}

async function loadCatalog() {
  try {
    const [providersRes, adaptersRes] = await Promise.all([
      cascadeService.listProviders({ limit: 1000 }),
      cascadeService.listAdapters(),
    ])
    providers.value = providersRes.data
    adapters.value = adaptersRes.data
  } catch {
    // Best-effort: without the catalog the badge just renders the code (no logo).
  }
}

const filters = reactive({ provider_code: '', success: '', order_id: '', request_id: '' })

const successOptions = [
  { value: '', label: 'Все' },
  { value: 'true', label: 'Успешные' },
  { value: 'false', label: 'Ошибки' },
]

const filterCount = computed(
  () => [filters.provider_code, filters.success, filters.order_id, filters.request_id].filter(Boolean).length,
)

// ClickHouse rows have no single PK; synthesize a stable key for the table.
const rows = computed(() =>
  items.value.map((r, i) => ({ ...r, _key: `${r.ts}-${r.request_id}-${i}` })),
)

const columns: Column[] = [
  { key: 'ts', label: 'Дата' },
  { key: 'provider_code', label: 'Провайдер' },
  { key: 'request_type', label: 'Тип' },
  { key: 'method', label: 'Метод' },
  { key: 'url', label: 'URL' },
  { key: 'response_status', label: 'Код' },
  { key: 'success', label: 'Результат' },
  { key: 'provider_latency_ms', label: 'Провайдер', align: 'right' },
  { key: 'e2e_ms', label: 'Всего', align: 'right' },
]

type BadgeColor = 'success' | 'danger' | 'warning' | 'info' | 'default' | 'gold'

// Request-type tabs (mirrors the backend request_type values).
const typeTabs = [
  { value: '', label: 'Все' },
  { value: 'payin', label: 'Payin' },
  { value: 'cancel', label: 'Cancel' },
  { value: 'balance', label: 'Balance' },
  { value: 'check', label: 'Check' },
  { value: 'upload', label: 'Upload' },
  { value: 'other', label: 'Прочее' },
]
const activeType = ref('')

function setType(v: string) {
  if (activeType.value === v) return
  activeType.value = v
  page.value = 1
  load()
}

function typeBadgeColor(t: string): BadgeColor {
  const map: Record<string, BadgeColor> = {
    payin: 'info', cancel: 'warning', balance: 'gold', check: 'info', upload: 'success',
  }
  return map[t] ?? 'default'
}

function statusClass(status: number | null | undefined): string {
  if (!status) return 'text-text-muted'
  if (status >= 200 && status < 300) return 'text-status-success'
  if (status >= 400 && status < 500) return 'text-status-warning'
  return 'text-status-danger'
}

const showDetail = ref(false)
const detail = ref<ProviderRequestLog | null>(null)
const detailTitle = computed(() =>
  detail.value ? `Запрос → ${detail.value.provider_code || 'провайдер'}` : 'Запрос провайдера',
)

function openDetail(row: ProviderRequestLog) {
  detail.value = row
  showDetail.value = true
}

function resetFilters() {
  page.value = 1
  filters.provider_code = ''
  filters.success = ''
  filters.order_id = ''
  filters.request_id = ''
  load()
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = {
      skip: (page.value - 1) * perPage.value,
      limit: perPage.value,
    }
    if (filters.provider_code) params.provider_code = filters.provider_code.trim()
    if (filters.success !== '') params.success = filters.success === 'true'
    if (filters.order_id) params.order_id = filters.order_id.trim()
    if (filters.request_id) params.request_id = filters.request_id.trim()
    if (activeType.value) params.request_type = activeType.value

    const { data } = await cascadeService.listProviderRequests(params)
    items.value = data
    totalPages.value = data.length < perPage.value ? page.value : page.value + 1
  } catch {
    toast.error('Ошибка загрузки запросов провайдеров')
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadCatalog()
  load()
})
</script>
