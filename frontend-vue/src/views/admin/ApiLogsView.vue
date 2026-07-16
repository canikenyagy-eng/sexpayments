<template>
  <div>
    <PageHeader title="API Запросы" />

    <!-- Tabs -->
    <div class="mb-4 flex gap-1 overflow-x-auto rounded-xl bg-bg-surface p-1 no-scrollbar">
      <button
        v-for="t in tabs"
        :key="t.key"
        class="shrink-0 rounded-lg px-4 py-2 text-sm font-bold transition"
        :class="activeTab === t.key ? 'bg-accent/15 text-accent' : 'text-text-muted hover:text-text-main'"
        @click="activeTab = t.key; page = 1; load()"
      >
        {{ t.label }}
      </button>
    </div>

    <div class="mb-4 flex items-center gap-2">
      <BaseFilter :active-count="activeFilterCount" @apply="page = 1; load()" @reset="resetFilters">
        <BaseInput v-model="filters.merchant_id" label="Merchant ID" placeholder="123" />
        <BaseSelect v-model="filters.method" label="Метод" :options="methodOptions" />
      </BaseFilter>
      <BaseSort v-model="sort" :options="sortOptions" @change="page = 1; load()" />
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
      @row-click="row => openDetail(row as MerchantApiLog)"
      @page-change="p => { page = p; load() }"
      @per-page-change="n => { perPage = n; page = 1; load() }"
    >
      <template #cell-request_id="{ value }">
        <span class="font-mono text-xs text-text-muted" :title="value as string">
          {{ value ? String(value).slice(0, 8) : '—' }}
        </span>
      </template>
      <template #cell-method="{ value }">
        <BaseBadge :color="methodColor(value as string)">{{ value }}</BaseBadge>
      </template>
      <template #cell-url="{ value }">
        <span class="font-mono text-xs">{{ shortenUrl(value as string) }}</span>
      </template>
      <template #cell-response_status="{ row }">
        <span
          :class="['font-mono text-xs font-bold', statusClass(row.response_status as number | null)]"
        >
          {{ row.response_status ?? '—' }}
        </span>
      </template>
      <template #cell-response_time_ms="{ value }">
        <span class="text-xs text-text-muted">
          {{ value != null ? value + ' мс' : '—' }}
        </span>
      </template>
      <template #cell-created_at="{ value }">
        {{ formatDate(value) }}
      </template>
    </DataTable>

    <BaseModal v-model="showDetail" :title="`Запрос ${detail?.method ?? ''} ${shortenUrl(detail?.url ?? '')}`" size="lg">
      <div v-if="detail">
        <!-- Modal Tabs -->
        <div class="mb-4 flex gap-1 overflow-x-auto rounded-xl bg-bg-surface p-1 no-scrollbar">
          <button
            class="shrink-0 rounded-lg px-4 py-2 text-sm font-bold transition"
            :class="modalTab === 'http' ? 'bg-accent/15 text-accent' : 'text-text-muted hover:text-text-main'"
            @click="modalTab = 'http'"
          >
            HTTP
          </button>
          <button
            v-if="isOrderCreationUrl(detail.url, detail.method)"
            class="shrink-0 rounded-lg px-4 py-2 text-sm font-bold transition"
            :class="modalTab === 'debug' ? 'bg-accent/15 text-accent' : 'text-text-muted hover:text-text-main'"
            @click="modalTab = 'debug'"
          >
            Debug
          </button>
        </div>

        <!-- HTTP Tab -->
        <div v-if="modalTab === 'http'" class="space-y-5">
          <div class="grid grid-cols-2 gap-2 text-sm">
            <div><span class="text-text-muted">Метод:</span> <BaseBadge :color="methodColor(detail.method)">{{ detail.method }}</BaseBadge></div>
            <div><span class="text-text-muted">Статус:</span> <span :class="statusClass(detail.response_status)" class="font-mono font-bold">{{ detail.response_status ?? '—' }}</span></div>
            <div><span class="text-text-muted">URL:</span> <span class="font-mono text-xs text-text-main">{{ detail.url }}</span></div>
            <div><span class="text-text-muted">Merchant ID:</span> <span class="text-text-main">{{ detail.merchant_id ?? '—' }}</span></div>
            <div class="flex items-center gap-1">
              <span class="text-text-muted">Order ID:</span>
              <span v-if="parsedOrderId" class="font-mono text-xs text-text-main flex items-center gap-1">
                {{ parsedOrderId }}
                <button
                  type="button"
                  class="rounded p-1 text-text-muted transition hover:bg-bg-hover hover:text-text-main"
                  title="Копировать"
                  @click="copyOrderId(parsedOrderId)"
                >
                  <Copy class="h-3 w-3" />
                </button>
                <button
                  type="button"
                  class="rounded p-1 text-text-muted transition hover:bg-bg-hover hover:text-text-main"
                  title="Открыть ордер"
                  @click="openOrderModal(parsedOrderId)"
                >
                  <ExternalLink class="h-3 w-3" />
                </button>
              </span>
              <span v-else class="text-text-main">{{ detail.order_id ?? '—' }}</span>
            </div>
            <div><span class="text-text-muted">Дата:</span> <span class="text-text-main ml-1">{{ formatDate(detail.created_at) }}</span></div>
            <div v-if="detail.response_time_ms != null"><span class="text-text-muted">Время:</span> <span class="text-text-main ml-1">{{ detail.response_time_ms }} мс</span></div>
          </div>

          <div>
            <h4 class="mb-1 text-sm font-bold text-accent">Request Headers</h4>
            <pre class="rounded-lg bg-bg-card p-3 text-xs text-text-secondary overflow-x-auto" v-html="formatJson(detail.request_headers)"></pre>
          </div>

          <div>
            <h4 class="mb-1 text-sm font-bold text-accent">Request Body</h4>
            <pre class="rounded-lg bg-bg-card p-3 text-xs text-text-secondary overflow-x-auto" v-html="formatBody(detail.request_body)"></pre>
          </div>

          <div>
            <h4 class="mb-1 text-sm font-bold text-accent">Response Headers</h4>
            <pre class="rounded-lg bg-bg-card p-3 text-xs text-text-secondary overflow-x-auto" v-html="formatJson(detail.response_headers)"></pre>
          </div>

          <div>
            <h4 class="mb-1 text-sm font-bold text-accent">Response Body</h4>
            <pre class="rounded-lg bg-bg-card p-3 text-xs text-text-secondary overflow-x-auto" v-html="formatBody(detail.response_body)"></pre>
          </div>
        </div>

        <!-- Debug Tab -->
        <div v-else-if="modalTab === 'debug' && isOrderCreationUrl(detail.url, detail.method)">
          <div v-if="snapshotLoading" class="flex items-center gap-2 rounded-lg bg-bg-card p-4 text-sm text-text-muted">
            <span class="inline-block h-4 w-4 animate-spin rounded-full border-2 border-accent border-t-transparent" />
            Загрузка snapshot...
          </div>
          <div v-else-if="snapshot" class="space-y-4">
            <h4 class="text-sm font-bold text-accent">Snapshot создания ордера</h4>

            <!-- Result -->
            <div class="rounded-lg p-3" :class="snapshot.result.success ? 'bg-status-success/10' : 'bg-status-danger/10'">
              <div class="flex items-center gap-2 text-sm font-bold" :class="snapshot.result.success ? 'text-status-success' : 'text-status-danger'">
                {{ snapshot.result.success ? 'Успешно' : 'Ошибка' }}
                <span v-if="snapshot.result.selected_requisite_id" class="font-normal text-text-muted">
                  — реквизит #{{ snapshot.result.selected_requisite_id }}
                </span>
              </div>
              <div v-if="snapshot.result.error" class="mt-1 text-xs text-text-secondary">{{ snapshot.result.error }}</div>
            </div>

            <!-- Request data -->
            <div>
              <h5 class="mb-1 text-sm font-bold text-accent">Запрос</h5>
              <div class="grid grid-cols-2 gap-1 text-xs sm:grid-cols-4">
                <div v-for="(val, key) in snapshot.request_data" :key="key" class="rounded bg-bg-card px-2 py-1">
                  <span class="text-text-muted">{{ key }}:</span> <span class="text-text-main font-medium">{{ val ?? '—' }}</span>
                </div>
              </div>
            </div>

            <!-- Merchant -->
            <div>
              <h5 class="mb-1 text-sm font-bold text-accent">Мерчант</h5>
              <div class="grid grid-cols-2 gap-1 text-xs sm:grid-cols-3">
                <div class="rounded bg-bg-card px-2 py-1"><span class="text-text-muted">ID:</span> {{ snapshot.merchant_snapshot.id }}</div>
                <div class="rounded bg-bg-card px-2 py-1"><span class="text-text-muted">Статус:</span> {{ snapshot.merchant_snapshot.status }}</div>
                <div class="rounded bg-bg-card px-2 py-1"><span class="text-text-muted">Валюта:</span> {{ snapshot.merchant_snapshot.currency }}</div>
                <div class="rounded bg-bg-card px-2 py-1"><span class="text-text-muted">TTL:</span> {{ snapshot.merchant_snapshot.order_ttl_seconds }}с</div>
                <div class="rounded bg-bg-card px-2 py-1 col-span-2"><span class="text-text-muted">Fees:</span> {{ JSON.stringify(snapshot.merchant_snapshot.fees) }}</div>
              </div>
            </div>

            <!-- Rate -->
            <div v-if="snapshot.rate_snapshot">
              <h5 class="mb-1 text-sm font-bold text-accent">Курс</h5>
              <div class="grid grid-cols-2 gap-1 text-xs sm:grid-cols-4">
                <div class="rounded bg-bg-card px-2 py-1 flex items-center gap-1"><span class="text-text-muted">Источник:</span> <RateSourceBadge :source="snapshot.rate_snapshot.source" /></div>
                <div class="rounded bg-bg-card px-2 py-1"><span class="text-text-muted">Курс:</span> <span class="font-medium">{{ snapshot.rate_snapshot.current_rate }}</span></div>
                <div class="rounded bg-bg-card px-2 py-1"><span class="text-text-muted">Валюта:</span> {{ snapshot.rate_snapshot.fiat_currency }}</div>
                <div class="rounded bg-bg-card px-2 py-1"><span class="text-text-muted">Обновлён:</span> {{ snapshot.rate_snapshot.last_updated_at ? formatDate(snapshot.rate_snapshot.last_updated_at) : '—' }}</div>
              </div>
            </div>
            <div v-else class="text-xs text-status-danger">Курс не найден</div>

            <!-- Traders -->
            <div>
              <h5 class="mb-1 text-sm font-bold text-accent">Трейдеры ({{ snapshot.traders_snapshot.length }})</h5>
              <div v-if="snapshot.traders_snapshot.length" class="max-h-40 overflow-y-auto rounded-lg border border-border">
                <table class="w-full text-xs">
                  <thead class="sticky top-0 z-10 border-b border-border bg-bg-surface text-text-muted shadow-[0_1px_0_theme(colors.border)]">
                    <tr>
                      <th class="px-2 py-1 text-left">ID</th>
                      <th class="px-2 py-1 text-left">User ID</th>
                      <th class="px-2 py-1 text-left">Статус</th>
                      <th class="px-2 py-1 text-left">Payin</th>
                      <th class="px-2 py-1 text-left">Метод</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr v-for="t in snapshot.traders_snapshot" :key="t.trader_id" class="border-t border-border">
                      <td class="px-2 py-1">{{ t.trader_id }}</td>
                      <td class="px-2 py-1">{{ t.user_id }}</td>
                      <td class="px-2 py-1">
                        <span :class="t.status === 'enabled' ? 'text-status-success' : 'text-status-danger'">{{ t.status }}</span>
                      </td>
                      <td class="px-2 py-1">
                        <span :class="t.is_payin_active ? 'text-status-success' : 'text-status-danger'">{{ t.is_payin_active ? 'да' : 'нет' }}</span>
                      </td>
                      <td class="px-2 py-1 text-text-muted">
                        <span v-if="t.method_config">fee {{ t.method_config.fee }}%, {{ t.method_config.min_amount }}-{{ t.method_config.max_amount }}</span>
                        <span v-else>—</span>
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-else class="text-xs text-text-muted">Нет трейдеров</div>
            </div>

            <!-- Candidates -->
            <div>
              <h5 class="mb-1 text-sm font-bold text-accent">
                Подходящие реквизиты ({{ snapshot.candidates.length }})
              </h5>
              <div v-if="snapshot.candidates.length" class="max-h-40 overflow-y-auto rounded-lg border border-border">
                <table class="w-full text-xs">
                  <thead class="sticky top-0 z-10 border-b border-border bg-bg-surface text-text-muted shadow-[0_1px_0_theme(colors.border)]">
                    <tr>
                      <th class="px-2 py-1 text-left">ID</th>
                      <th class="px-2 py-1 text-left">Трейдер</th>
                      <th class="px-2 py-1 text-left">Банк</th>
                      <th class="px-2 py-1 text-left">Лимиты</th>
                      <th class="px-2 py-1 text-left">Обороты</th>
                      <th class="px-2 py-1 text-left">Ордера</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="r in snapshot.candidates"
                      :key="r.id"
                      class="border-t border-border"
                      :class="snapshot.result.selected_requisite_id === r.id ? 'bg-status-success/10' : ''"
                    >
                      <td class="px-2 py-1">
                        {{ r.id }}
                        <span v-if="snapshot.result.selected_requisite_id === r.id" class="ml-1 text-accent">*</span>
                      </td>
                      <td class="px-2 py-1">{{ r.user_id }}</td>
                      <td class="px-2 py-1">{{ r.bank_name }}</td>
                      <td class="px-2 py-1 text-text-muted">
                        <template v-if="r.limits">{{ r.limits.limit_min_transaction }}-{{ r.limits.limit_max_transaction }}</template>
                        <template v-else>—</template>
                      </td>
                      <td class="px-2 py-1 text-text-muted">
                        <template v-if="r.limits">д: {{ r.limits.current_daily_turnover }}/{{ r.limits.limit_daily }}, м: {{ r.limits.current_monthly_turnover }}/{{ r.limits.limit_monthly }}</template>
                      </td>
                      <td class="px-2 py-1">{{ r.active_orders }}<span v-if="r.limits?.limit_max_concurrent_orders" class="text-text-muted">/{{ r.limits.limit_max_concurrent_orders }}</span></td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-else class="text-xs text-status-warning">Нет подходящих реквизитов</div>
            </div>

            <!-- Cascade attempts (loaded only when we have an order_id —
                 ordered chronologically, exactly one row should be ``won``
                 if the cascade routed the order) -->
            <div v-if="detail?.order_id">
              <h5 class="mb-1 text-sm font-bold text-accent">
                Cascade попытки ({{ cascadeAttempts.length }})
              </h5>
              <div v-if="cascadeAttemptsLoading" class="flex items-center gap-2 rounded-lg bg-bg-card p-3 text-xs text-text-muted">
                <span class="inline-block h-3 w-3 animate-spin rounded-full border-2 border-accent border-t-transparent" />
                Загрузка попыток каскада…
              </div>
              <div v-else-if="cascadeAttempts.length" class="max-h-48 overflow-y-auto rounded-lg border border-border">
                <table class="w-full text-xs">
                  <thead class="sticky top-0 z-10 border-b border-border bg-bg-surface text-text-muted shadow-[0_1px_0_theme(colors.border)]">
                    <tr>
                      <th class="px-2 py-1 text-left">Провайдер</th>
                      <th class="px-2 py-1 text-left">Tier</th>
                      <th class="px-2 py-1 text-left">Статус</th>
                      <th class="px-2 py-1 text-left">External ID</th>
                      <th class="px-2 py-1 text-left">Latency</th>
                      <th class="px-2 py-1 text-left">Причина / ошибка</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr
                      v-for="a in cascadeAttempts"
                      :key="a.id"
                      class="border-t border-border"
                      :class="a.status === 'won' ? 'bg-status-success/10' : ''"
                    >
                      <td class="px-2 py-1 font-mono">#{{ a.provider_id }}</td>
                      <td class="px-2 py-1 text-text-muted">{{ a.tier ?? '—' }}</td>
                      <td class="px-2 py-1">
                        <span :class="attemptStatusClass(a.status)">{{ a.status }}</span>
                      </td>
                      <td class="px-2 py-1 font-mono text-text-muted">{{ a.external_order_id ?? '—' }}</td>
                      <td class="px-2 py-1 text-text-muted">{{ a.latency_ms != null ? a.latency_ms + ' мс' : '—' }}</td>
                      <td class="px-2 py-1 text-text-muted">
                        {{ a.refusal_reason || a.error_code || a.error_message || '—' }}
                      </td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div v-else class="text-xs text-text-muted">Каскад не вызывался для этого ордера</div>
            </div>

          </div>
          <div v-else class="rounded-lg bg-bg-card p-3 text-xs text-text-muted">
            Snapshot не найден для этого запроса
          </div>
        </div>
      </div>
    </BaseModal>

    <AdminOrderDebugModal ref="orderModalRef" />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { Copy, ExternalLink } from 'lucide-vue-next'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseFilter from '@/components/ui/BaseFilter.vue'
import BaseSort from '@/components/ui/BaseSort.vue'
import type { SortValue } from '@/components/ui/BaseSort.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import RateSourceBadge from '@/components/ui/RateSourceBadge.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import AdminOrderDebugModal from '@/components/modals/AdminOrderDebugModal.vue'
import { auditService } from '@/api/services/audit.service'
import { cascadeService } from '@/api/services/cascade.service'
import { useToast } from '@/composables/useToast'
import { formatDate, formatJson, formatBody, copyToClipboard } from '@/utils/format'
import type { CascadeOrderAttempt, MerchantApiLog, OrderCreationSnapshot } from '@/types'

const API_PREFIXES = ['/api/merchant/v1', '/api/bot/v1']

const tabs = [
  { key: 'all', label: 'Все' },
  { key: 'orders', label: 'Ордера' },
  { key: 'payments', label: 'Платежи' },
  { key: 'rates', label: 'Курсы' },
  { key: 'profile', label: 'Профиль' },
  { key: 'callbacks', label: 'Коллбеки' },
  { key: 'bot', label: 'Бот' },
  { key: 'unknown', label: 'Неизвестные' },
]

const toast = useToast()

const loading = ref(false)
const logs = ref<MerchantApiLog[]>([])
const rows = computed(() =>
  logs.value.map((r, i) => ({ ...r, _key: `${r.request_id ?? ''}-${r.created_at}-${i}` })),
)
const page = ref(1)
const perPage = ref(50)
const totalPages = ref(1)
const activeTab = ref('all')
const filters = reactive({ merchant_id: '', method: '' })
const sort = ref<SortValue>({ key: '', order: 'desc' })

const sortOptions = [
  { key: 'created_at', label: 'Дата' },
  { key: 'response_status', label: 'Статус' },
  { key: 'method', label: 'Метод' },
  { key: 'merchant_id', label: 'Merchant' },
]

const activeFilterCount = computed(() =>
  [filters.merchant_id, filters.method].filter(Boolean).length,
)

function resetFilters() {
  page.value = 1
  filters.merchant_id = ''
  filters.method = ''
  load()
}

const showDetail = ref(false)
const detail = ref<MerchantApiLog | null>(null)
const snapshot = ref<OrderCreationSnapshot | null>(null)
const snapshotLoading = ref(false)
const modalTab = ref<'http' | 'debug'>('http')
const orderModalRef = ref<InstanceType<typeof AdminOrderDebugModal> | null>(null)
const cascadeAttempts = ref<CascadeOrderAttempt[]>([])
const cascadeAttemptsLoading = ref(false)

function attemptStatusClass(status: string): string {
  switch (status) {
    case 'won': return 'text-status-success font-bold'
    case 'lost': case 'refused': return 'text-text-muted'
    case 'failed': case 'timeout': return 'text-status-danger'
    case 'in_flight': return 'text-status-warning'
    default: return 'text-text-main'
  }
}

const parsedOrderId = computed(() => {
  if (!detail.value) return null
  try {
    if (detail.value.response_body) {
      const body = JSON.parse(detail.value.response_body)
      if (body && body.id && typeof body.id === 'string' && body.id.length > 20) {
        return body.id
      }
    }
  } catch {}
  return null
})

async function copyOrderId(id: string) {
  try {
    await copyToClipboard(id)
    toast.success('Order ID скопирован')
  } catch {
    // ignore
  }
}

function openOrderModal(id: string) {
  orderModalRef.value?.open(id)
}

const columns: Column[] = [
  { key: 'request_id', label: 'Request ID' },
  { key: 'method', label: 'Метод' },
  { key: 'url', label: 'URL' },
  { key: 'merchant_id', label: 'Merchant' },
  { key: 'response_status', label: 'Статус' },
  { key: 'response_time_ms', label: 'Время' },
  { key: 'created_at', label: 'Дата' },
]

const methodOptions = [
  { value: '', label: 'Все' },
  { value: 'GET', label: 'GET' },
  { value: 'POST', label: 'POST' },
  { value: 'PUT', label: 'PUT' },
  { value: 'PATCH', label: 'PATCH' },
  { value: 'DELETE', label: 'DELETE' },
]

function shortenUrl(url: string): string {
  for (const prefix of API_PREFIXES) {
    if (url.startsWith(prefix)) return url.slice(prefix.length)
  }
  return url
}

function methodColor(method: string): 'success' | 'warning' | 'danger' | 'info' {
  const map: Record<string, 'success' | 'warning' | 'danger' | 'info'> = {
    GET: 'success',
    POST: 'info',
    PUT: 'warning',
    PATCH: 'warning',
    DELETE: 'danger',
  }
  return map[method] ?? 'info'
}

function statusClass(status: number | null | undefined): string {
  if (!status) return 'text-text-muted'
  if (status >= 200 && status < 300) return 'text-status-success'
  if (status >= 400 && status < 500) return 'text-status-warning'
  return 'text-status-danger'
}

function isOrderCreationUrl(url: string, method?: string): boolean {
  if (url.includes('/payin')) return true
  if ((!method || method === 'POST') && /\/api\/bot\/v1\/merchants\/\d+\/orders\/?$/.test(url)) return true
  return false
}

async function openDetail(row: MerchantApiLog) {
  detail.value = row
  snapshot.value = null
  cascadeAttempts.value = []
  modalTab.value = 'http'
  showDetail.value = true

  if (isOrderCreationUrl(row.url, row.method)) {
    snapshotLoading.value = true
    try {
      const { data } = row.request_id
        ? await auditService.getSnapshot(row.request_id)
        : { data: null }
      snapshot.value = data
    } catch {
      snapshot.value = null
    } finally {
      snapshotLoading.value = false
    }

    // Cascade attempts are keyed by the integer order_id (UUID won't fit
    // the backend route). Skip silently when the request didn't produce
    // an order (validation/auth errors) — `cascade_order_attempts` simply
    // has nothing for it.
    if (row.order_id) {
      cascadeAttemptsLoading.value = true
      try {
        const { data } = await cascadeService.orderAttempts(row.order_id)
        cascadeAttempts.value = data
      } catch {
        cascadeAttempts.value = []
      } finally {
        cascadeAttemptsLoading.value = false
      }
    }
  }
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = { skip: (page.value - 1) * perPage.value, limit: perPage.value }
    if (filters.merchant_id) params.merchant_id = Number(filters.merchant_id)
    if (filters.method) params.method = filters.method
    if (activeTab.value !== 'all') params.endpoint_group = activeTab.value
    if (sort.value.key) {
      params.sort_by = sort.value.key
      params.sort_order = sort.value.order
    }
    
    const { data } = await auditService.getApiLogs(params)
    logs.value = data
    totalPages.value = data.length < perPage.value ? page.value : page.value + 1
  } catch {
    toast.error('Ошибка загрузки API логов')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
