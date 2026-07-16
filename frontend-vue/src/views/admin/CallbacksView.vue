<template>
  <div>
    <PageHeader title="Колбэки" />

    <BaseTabs v-model="activeTab" :tabs="tabs">
      <!-- ─── Мерчанты — outbound callbacks мерчантам (наши POST к ним) ─── -->
      <template #merchants>
        <div class="mb-4 flex items-center gap-2 pt-2">
          <BaseFilter
            :active-count="merchantFilterCount"
            @apply="merchantPage = 1; loadMerchant()"
            @reset="resetMerchantFilters"
          >
            <BaseInput v-model="merchantFilters.order_id" label="Order ID" placeholder="123" />
            <BaseSelect v-model="merchantFilters.is_successful" label="Статус" :options="successOptions" />
          </BaseFilter>
          <BaseSort v-model="merchantSort" :options="merchantSortOptions" @change="merchantPage = 1; loadMerchant()" />
        </div>

        <DataTable
          :columns="merchantColumns"
          :rows="merchantAttempts"
          :loading="merchantLoading"
          row-key="id"
          :current-page="merchantPage"
          :total-pages="merchantTotalPages"
          :per-page="merchantPerPage"
          clickable
          @row-click="row => openMerchantDetail(row as CallbackAttempt)"
          @page-change="p => { merchantPage = p; loadMerchant() }"
          @per-page-change="n => { merchantPerPage = n; merchantPage = 1; loadMerchant() }"
        >
          <template #cell-is_successful="{ value }">
            <BaseBadge :color="value ? 'success' : 'danger'">
              {{ value ? 'Успешно' : 'Ошибка' }}
            </BaseBadge>
          </template>
          <template #cell-url="{ value }">
            <span class="font-mono text-xs">{{ value }}</span>
          </template>
          <template #cell-response_status="{ value }">
            <span :class="['font-mono text-xs font-bold', statusClass(value as number | null)]">
              {{ value ?? '—' }}
            </span>
          </template>
          <template #cell-created_at="{ value }">
            {{ formatDate(value) }}
          </template>
        </DataTable>
      </template>

      <!-- ─── Провайдеры — inbound callbacks от провайдеров (их POST к нам) ─── -->
      <template #providers>
        <div class="mb-4 flex items-center gap-2 pt-2">
          <BaseFilter
            :active-count="providerFilterCount"
            @apply="providerPage = 1; loadProvider()"
            @reset="resetProviderFilters"
          >
            <ProviderSelect v-model="providerFilters.provider_code" label="Провайдер" />
            <BaseInput v-model="providerFilters.order_id" label="Order ID" placeholder="123" />
            <BaseInput v-model="providerFilters.external_order_id" label="External ID" placeholder="uuid…" />
            <BaseSelect
              v-model="providerFilters.signature_valid"
              label="Подпись"
              :options="signatureOptions"
            />
          </BaseFilter>
        </div>

        <DataTable
          :columns="providerColumns"
          :rows="providerRows"
          :loading="providerLoading"
          row-key="_key"
          :current-page="providerPage"
          :total-pages="providerTotalPages"
          :per-page="providerPerPage"
          clickable
          @row-click="row => openProviderDetail(row as ProviderCallbackAttempt)"
          @page-change="p => { providerPage = p; loadProvider() }"
          @per-page-change="n => { providerPerPage = n; providerPage = 1; loadProvider() }"
        >
          <template #cell-provider_code="{ value }">
            <BaseBadge color="info">{{ value }}</BaseBadge>
          </template>
          <template #cell-signature_valid="{ value }">
            <BaseBadge :color="value ? 'success' : 'danger'">
              {{ value ? 'OK' : 'нет' }}
            </BaseBadge>
          </template>
          <template #cell-parsed_status="{ value }">
            <span class="font-mono text-xs">{{ value ?? '—' }}</span>
          </template>
          <template #cell-external_order_id="{ value }">
            <span class="font-mono text-xs">{{ value ?? '—' }}</span>
          </template>
          <template #cell-response_status="{ value }">
            <span :class="['font-mono text-xs font-bold', statusClass(value as number | null)]">
              {{ value ?? '—' }}
            </span>
          </template>
          <template #cell-processing_ms="{ value }">
            <span class="font-mono text-xs">{{ value ? `${value} мс` : '—' }}</span>
          </template>
          <template #cell-created_at="{ value }">
            {{ formatDate(value) }}
          </template>
        </DataTable>
      </template>
    </BaseTabs>

    <!-- Merchant callback detail modal -->
    <BaseModal v-model="showMerchantDetail" :title="`Колбэк #${merchantDetail?.id}`" size="lg">
      <div v-if="merchantDetail" class="space-y-5">
        <div class="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span class="text-text-muted">Order ID:</span>
            <span class="ml-1 font-mono text-text-main">{{ merchantDetail.order_id }}</span>
          </div>
          <div>
            <span class="text-text-muted">Статус:</span>
            <span :class="['ml-1 font-mono font-bold', statusClass(merchantDetail.response_status)]">
              {{ merchantDetail.response_status ?? '—' }}
            </span>
          </div>
          <div>
            <span class="text-text-muted">URL:</span>
            <span class="ml-1 break-all font-mono text-xs text-text-main">{{ merchantDetail.url }}</span>
          </div>
          <div>
            <span class="text-text-muted">Попытка:</span>
            <span class="ml-1 text-text-main">{{ merchantDetail.attempt_number }}</span>
          </div>
          <div>
            <span class="text-text-muted">Результат:</span>
            <BaseBadge :color="merchantDetail.is_successful ? 'success' : 'danger'" class="ml-1">
              {{ merchantDetail.is_successful ? 'Успешно' : 'Ошибка' }}
            </BaseBadge>
          </div>
          <div>
            <span class="text-text-muted">Дата:</span>
            <span class="ml-1 text-text-main">{{ formatDate(merchantDetail.created_at) }}</span>
          </div>
        </div>

        <div>
          <h4 class="mb-1 text-sm font-bold text-accent">Request Headers</h4>
          <pre class="overflow-x-auto rounded-lg bg-bg-card p-3 text-xs text-text-secondary" v-html="formatJson(merchantDetail.request_headers)"></pre>
        </div>

        <div>
          <h4 class="mb-1 text-sm font-bold text-accent">Request Payload</h4>
          <pre class="overflow-x-auto rounded-lg bg-bg-card p-3 text-xs text-text-secondary" v-html="formatJson(merchantDetail.request_payload)"></pre>
        </div>

        <div>
          <h4 class="mb-1 text-sm font-bold text-accent">Response Body</h4>
          <pre class="overflow-x-auto rounded-lg bg-bg-card p-3 text-xs text-text-secondary" v-html="formatBody(merchantDetail.response_body)"></pre>
        </div>
      </div>
    </BaseModal>

    <!-- Provider callback detail modal -->
    <BaseModal v-model="showProviderDetail" :title="`Колбэк провайдера · ${providerDetail?.provider_code ?? ''}`" size="lg">
      <div v-if="providerDetail" class="space-y-5">
        <div class="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span class="text-text-muted">Провайдер:</span>
            <span class="ml-1 font-mono text-text-main">
              {{ providerDetail.provider_code }}<span v-if="providerDetail.provider_id"> #{{ providerDetail.provider_id }}</span>
            </span>
          </div>
          <div>
            <span class="text-text-muted">Подпись:</span>
            <BaseBadge :color="providerDetail.signature_valid ? 'success' : 'danger'" class="ml-1">
              {{ providerDetail.signature_valid ? 'OK' : 'нет' }}
            </BaseBadge>
          </div>
          <div>
            <span class="text-text-muted">Распарсенный статус:</span>
            <span class="ml-1 font-mono text-text-main">{{ providerDetail.parsed_status ?? '—' }}</span>
          </div>
          <div>
            <span class="text-text-muted">Наш HTTP ответ:</span>
            <span :class="['ml-1 font-mono font-bold', statusClass(providerDetail.response_status)]">
              {{ providerDetail.response_status ?? '—' }}
            </span>
          </div>
          <div>
            <span class="text-text-muted">External Order ID:</span>
            <span class="ml-1 break-all font-mono text-xs text-text-main">
              {{ providerDetail.external_order_id ?? '—' }}
            </span>
          </div>
          <div>
            <span class="text-text-muted">Наш Order ID:</span>
            <span class="ml-1 font-mono text-text-main">{{ providerDetail.order_id ?? '—' }}</span>
          </div>
          <div>
            <span class="text-text-muted">Обработка:</span>
            <span class="ml-1 font-mono text-text-main">
              {{ providerDetail.processing_ms != null ? `${providerDetail.processing_ms} мс` : '—' }}
            </span>
          </div>
          <div>
            <span class="text-text-muted">Дата:</span>
            <span class="ml-1 text-text-main">{{ formatDate(providerDetail.created_at) }}</span>
          </div>
          <div class="col-span-2">
            <span class="text-text-muted">Request ID:</span>
            <span class="ml-1 break-all font-mono text-xs text-text-main">{{ providerDetail.request_id ?? '—' }}</span>
          </div>
        </div>

        <div v-if="providerDetail.error_message">
          <h4 class="mb-1 text-sm font-bold text-status-danger">Ошибка обработки</h4>
          <pre class="overflow-x-auto rounded-lg bg-status-danger/10 p-3 text-xs text-status-danger">{{ providerDetail.error_message }}</pre>
        </div>

        <div>
          <h4 class="mb-1 text-sm font-bold text-accent">Request Headers</h4>
          <pre class="overflow-x-auto rounded-lg bg-bg-card p-3 text-xs text-text-secondary" v-html="formatJson(providerDetail.request_headers)"></pre>
        </div>

        <div>
          <h4 class="mb-1 text-sm font-bold text-accent">Request Body (raw)</h4>
          <pre class="overflow-x-auto rounded-lg bg-bg-card p-3 text-xs text-text-secondary">{{ providerDetail.request_body ?? '—' }}</pre>
        </div>

        <div>
          <h4 class="mb-1 text-sm font-bold text-accent">Наш ответ</h4>
          <pre class="overflow-x-auto rounded-lg bg-bg-card p-3 text-xs text-text-secondary" v-html="formatBody(providerDetail.response_body)"></pre>
        </div>
      </div>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import ProviderSelect from '@/components/ui/ProviderSelect.vue'
import BaseFilter from '@/components/ui/BaseFilter.vue'
import BaseSort from '@/components/ui/BaseSort.vue'
import type { SortValue } from '@/components/ui/BaseSort.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseTabs from '@/components/ui/BaseTabs.vue'
import type { Tab } from '@/components/ui/BaseTabs.vue'
import { callbacksService } from '@/api/services/callbacks.service'
import { cascadeService } from '@/api/services/cascade.service'
import { useToast } from '@/composables/useToast'
import { formatDate, formatJson, formatBody } from '@/utils/format'
import type { CallbackAttempt, ProviderCallbackAttempt } from '@/types'

const toast = useToast()

const activeTab = ref('merchants')
const tabs = computed<Tab[]>(() => [
  { key: 'merchants', label: 'Мерчанты' },
  { key: 'providers', label: 'Провайдеры' },
])

const successOptions = [
  { value: '', label: 'Все' },
  { value: 'true', label: 'Успешные' },
  { value: 'false', label: 'Ошибки' },
]

const signatureOptions = [
  { value: '', label: 'Все' },
  { value: 'true', label: 'Подпись OK' },
  { value: 'false', label: 'Подпись не прошла' },
]

function statusClass(status: number | null | undefined): string {
  if (!status) return 'text-text-muted'
  if (status >= 200 && status < 300) return 'text-status-success'
  if (status >= 400 && status < 500) return 'text-status-warning'
  return 'text-status-danger'
}

// ─── Merchants tab — outbound callbacks ──────────────────────────────────

const merchantLoading = ref(false)
const merchantAttempts = ref<CallbackAttempt[]>([])
const merchantPage = ref(1)
const merchantPerPage = ref(50)
const merchantTotalPages = ref(1)
const merchantFilters = reactive({ order_id: '', is_successful: '' })
const merchantSort = ref<SortValue>({ key: '', order: 'desc' })

const merchantSortOptions = [
  { key: 'created_at', label: 'Дата' },
]

const merchantFilterCount = computed(() =>
  [merchantFilters.order_id, merchantFilters.is_successful].filter(Boolean).length,
)

function resetMerchantFilters() {
  merchantPage.value = 1
  merchantFilters.order_id = ''
  merchantFilters.is_successful = ''
  loadMerchant()
}

const showMerchantDetail = ref(false)
const merchantDetail = ref<CallbackAttempt | null>(null)

function openMerchantDetail(row: CallbackAttempt) {
  merchantDetail.value = row
  showMerchantDetail.value = true
}

const merchantColumns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'order_id', label: 'Order ID' },
  { key: 'url', label: 'URL' },
  { key: 'response_status', label: 'HTTP статус' },
  { key: 'attempt_number', label: 'Попытка' },
  { key: 'is_successful', label: 'Результат' },
  { key: 'created_at', label: 'Дата' },
]

async function loadMerchant() {
  merchantLoading.value = true
  try {
    const params: Record<string, any> = {
      skip: (merchantPage.value - 1) * merchantPerPage.value,
      limit: merchantPerPage.value,
    }
    if (merchantFilters.order_id) params.order_id = Number(merchantFilters.order_id)
    if (merchantFilters.is_successful !== '') params.is_successful = merchantFilters.is_successful === 'true'
    if (merchantSort.value.key) params.sort_order = merchantSort.value.order

    const { data } = await callbacksService.listAttempts(params)
    merchantAttempts.value = data
    merchantTotalPages.value = data.length < merchantPerPage.value ? merchantPage.value : merchantPage.value + 1
  } catch {
    toast.error('Ошибка загрузки колбэков мерчантам')
  } finally {
    merchantLoading.value = false
  }
}

// ─── Providers tab — inbound callbacks ───────────────────────────────────

const providerLoading = ref(false)
const providerAttempts = ref<ProviderCallbackAttempt[]>([])
const providerPage = ref(1)
const providerPerPage = ref(50)
const providerTotalPages = ref(1)
const providerFilters = reactive({
  provider_code: '',
  order_id: '',
  external_order_id: '',
  signature_valid: '',
})

const providerFilterCount = computed(() =>
  [
    providerFilters.provider_code,
    providerFilters.order_id,
    providerFilters.external_order_id,
    providerFilters.signature_valid,
  ].filter(Boolean).length,
)

function resetProviderFilters() {
  providerPage.value = 1
  providerFilters.provider_code = ''
  providerFilters.order_id = ''
  providerFilters.external_order_id = ''
  providerFilters.signature_valid = ''
  loadProvider()
}

const showProviderDetail = ref(false)
const providerDetail = ref<ProviderCallbackAttempt | null>(null)

function openProviderDetail(row: ProviderCallbackAttempt) {
  providerDetail.value = row
  showProviderDetail.value = true
}

const providerColumns: Column[] = [
  { key: 'created_at', label: 'Дата' },
  { key: 'provider_code', label: 'Провайдер' },
  { key: 'parsed_status', label: 'Статус' },
  { key: 'external_order_id', label: 'External ID' },
  { key: 'order_id', label: 'Наш Order ID' },
  { key: 'signature_valid', label: 'Подпись' },
  { key: 'response_status', label: 'Наш HTTP' },
  { key: 'processing_ms', label: 'Обработка', align: 'right' },
]

// ClickHouse rows have no PK — synthesize a stable key for the table.
const providerRows = computed(() =>
  providerAttempts.value.map((r, i) => ({
    ...r,
    _key: `${r.created_at}-${r.request_id ?? ''}-${r.external_order_id ?? ''}-${i}`,
  })),
)

async function loadProvider() {
  providerLoading.value = true
  try {
    const params: Record<string, any> = {
      skip: (providerPage.value - 1) * providerPerPage.value,
      limit: providerPerPage.value,
    }
    if (providerFilters.provider_code) params.provider_code = providerFilters.provider_code.trim()
    if (providerFilters.order_id) params.order_id = Number(providerFilters.order_id)
    if (providerFilters.external_order_id) params.external_order_id = providerFilters.external_order_id.trim()
    if (providerFilters.signature_valid !== '') {
      params.signature_valid = providerFilters.signature_valid === 'true'
    }

    const { data } = await cascadeService.listProviderCallbacks(params)
    providerAttempts.value = data
    providerTotalPages.value = data.length < providerPerPage.value ? providerPage.value : providerPage.value + 1
  } catch {
    toast.error('Ошибка загрузки колбэков провайдеров')
  } finally {
    providerLoading.value = false
  }
}

onMounted(() => {
  loadMerchant()
  loadProvider()
})
</script>
