<template>
  <div>
    <PageHeader title="Споры">
      <template #actions>
        <BaseButton variant="gold" size="sm" @click="openCreate">Открыть спор</BaseButton>
      </template>
    </PageHeader>

    <div class="mb-4 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-4">
      <BaseInput v-model="filters.id_search" label="ID / UUID / external ID" placeholder="поиск по ID" />
      <BaseInput v-model="filters.trader_login" label="Логин трейдера" placeholder="trader" />
      <BaseInput v-model="filters.merchant_login" label="Логин мерчанта" placeholder="merchant" />
      <BaseSelect v-model="filters.status" label="Статус" :options="statusOptions" />
      <BaseSelect v-model="filters.reason" label="Причина" :options="reasonOptions" />
      <BaseSelect v-model="filters.payment_method" label="Метод" :options="methodOptions" />
      <BaseInput v-model="filters.amount_from" label="Сумма от (RUB)" type="number" placeholder="0" />
      <BaseInput v-model="filters.amount_to" label="Сумма до (RUB)" type="number" placeholder="∞" />
      <div class="flex items-end gap-2 lg:col-span-2">
        <BaseButton variant="gold" size="sm" @click="applyFilters">Применить</BaseButton>
        <BaseButton variant="dark" size="sm" @click="resetFilters">Сброс</BaseButton>
      </div>
    </div>

    <DataTable
      :columns="columns"
      :rows="sortedRows"
      row-key="id"
      clickable
      :loading="loading"
      :current-page="page"
      :total-pages="totalPages"
      :per-page="perPage"
      @page-change="p => { page = p; load() }"
      @per-page-change="onPerPageChange"
      @row-click="openDispute($event)"
    >
      <template #cell-uuid="{ row }">
        <UuidDisplay :value="row.uuid" />
      </template>
      <template #cell-amount="{ row }">
        {{ row.order_amount != null ? row.order_amount : '—' }} {{ row.order_currency || 'RUB' }}
      </template>
      <template #cell-payment_method="{ value }">
        <MethodBadge :method="value" />
      </template>
      <template #cell-dispute_status="{ row }">
        <DisputeStatusBadge :status="row.status" />
      </template>
      <template #cell-reason="{ value }">
        <BaseBadge color="info">{{ formatReason(value) }}</BaseBadge>
      </template>
      <template #cell-merchant_login="{ value }">
        <span class="text-xs">{{ value ?? '—' }}</span>
      </template>
      <template #cell-trader_login="{ value }">
        <span class="text-xs">{{ value ?? '—' }}</span>
      </template>
      <template #cell-created_at="{ value }">
        {{ value ? formatDate(value) : '—' }}
      </template>
    </DataTable>

    <!-- Dispute management modal -->
    <BaseModal v-model="showDispute" title="Управление спором" size="lg">
      <div v-if="activeDispute" class="space-y-5">
        <div class="grid grid-cols-2 gap-x-6 gap-y-3 rounded-xl bg-bg-card p-4 text-sm">
          <div>
            <span class="text-text-muted">UUID:</span>
            <UuidDisplay :value="activeDispute.uuid" :truncate="false" show-icon class="ml-2" />
          </div>
          <div>
            <span class="text-text-muted">Создан:</span>
            <span class="ml-2 text-text-main">{{ activeDispute.created_at ? formatDate(activeDispute.created_at) : '—' }}</span>
          </div>
          <div>
            <span class="text-text-muted">Статус:</span>
            <DisputeStatusBadge class="ml-2" :status="activeDispute.status" />
          </div>
          <div>
            <span class="text-text-muted">Причина:</span>
            <BaseBadge class="ml-2" color="info">{{ formatReason(activeDispute.reason) }}</BaseBadge>
          </div>
          <div v-if="activeDispute.substatus" class="col-span-2">
            <span class="text-text-muted">Премодерация:</span>
            <BaseBadge class="ml-2" color="warning">{{ substatusLabel(activeDispute.substatus) }}</BaseBadge>
          </div>
          <div>
            <span class="text-text-muted">Метод:</span>
            <MethodBadge class="ml-2" :method="activeDispute.order_payment_method" />
          </div>
          <div>
            <span class="text-text-muted">Сумма:</span>
            <strong class="ml-2 text-text-main">
              {{ activeDispute.order_amount ?? '—' }} RUB
            </strong>
          </div>
          <div>
            <span class="text-text-muted">Мерчант:</span>
            <span class="ml-2">{{ activeDispute.merchant_login ?? `#${activeDispute.merchant_id}` }}</span>
          </div>
          <div>
            <span class="text-text-muted">Трейдер:</span>
            <span class="ml-2">{{ activeDispute.trader_login ?? '—' }}</span>
          </div>
          <div v-if="activeDispute.resolved_at" class="col-span-2">
            <span class="text-text-muted">Решение:</span>
            <p class="mt-1 text-text-main">{{ activeDispute.resolution_text || '—' }}</p>
          </div>
        </div>

        <DisputeEvidence
          :items="evidence"
          :loading="evidenceLoading"
          :resolve-url="adminEvidenceUrl"
        />

        <div class="flex flex-wrap gap-3">
          <template v-if="activeDispute.status === 'open'">
            <BaseButton variant="success" :loading="actionLoading" @click="openResolveModal">
              Решить в пользу мерчанта
            </BaseButton>
            <BaseButton variant="danger" :loading="actionLoading" @click="openRejectModal">
              Отклонить (в пользу трейдера)
            </BaseButton>
          </template>
          <BaseButton variant="ghost" @click="viewDebug(activeDispute)">
            Открыть debug ордера
          </BaseButton>
        </div>
      </div>
    </BaseModal>

    <BaseModal v-model="showResolution" :title="resolutionMode === 'resolve' ? 'Решить спор — в пользу мерчанта' : 'Отклонить спор — в пользу трейдера'">
      <div class="space-y-4">
        <p v-if="resolutionMode === 'resolve'" class="text-sm text-text-muted">
          Ордер перейдёт в статус <strong>SUCCESS</strong>. Средства будут переведены мерчанту.
        </p>
        <p v-else class="text-sm text-text-muted">
          Ордер перейдёт в статус <strong>FAILED</strong> (или <strong>REFUNDED</strong>, если ордер уже был успешно завершён). Средства будут возвращены трейдеру.
        </p>
        <div class="space-y-1.5">
          <label class="block text-sm font-semibold text-text-secondary">Текст решения</label>
          <textarea
            v-model="resolutionText"
            class="input-field w-full resize-none text-sm"
            rows="3"
            placeholder="Опишите основание принятого решения…"
          />
        </div>
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showResolution = false">Отмена</BaseButton>
        <BaseButton
          :variant="resolutionMode === 'resolve' ? 'success' : 'danger'"
          :loading="actionLoading"
          :disabled="!resolutionText.trim()"
          @click="submitResolution"
        >
          {{ resolutionMode === 'resolve' ? 'Подтвердить решение' : 'Отклонить спор' }}
        </BaseButton>
      </template>
    </BaseModal>

    <!-- Create dispute -->
    <BaseModal v-model="showCreate" title="Открыть спор" size="lg">
      <div class="space-y-4">
        <BaseInput v-model="createForm.orderUuid" label="UUID ордера" placeholder="00000000-0000-0000-0000-000000000000" />
        <BaseSelect v-model="createForm.reason" label="Причина" :options="createReasonOptions" />
        <div>
          <label class="block text-sm font-semibold text-text-secondary">Файлы (необязательно)</label>
          <input
            type="file"
            multiple
            accept="image/jpeg,image/png,image/webp,application/pdf,video/mp4,video/quicktime"
            class="mt-1.5 block w-full text-sm text-text-muted file:mr-3 file:rounded-lg file:border-0 file:bg-bg-hover file:px-3 file:py-1.5 file:text-text-main"
            @change="onCreateFiles"
          />
          <p class="mt-1 text-xs text-text-muted">Изображения, PDF или видео, до 10 МБ каждый.</p>
        </div>
        <div>
          <label class="block text-sm font-semibold text-text-secondary">Ссылки на файлы (необязательно)</label>
          <textarea
            v-model="createForm.urlsText"
            rows="3"
            class="input-field mt-1.5"
            placeholder="По одной ссылке на строку"
          />
        </div>
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showCreate = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="creating" :disabled="!createForm.orderUuid.trim()" @click="submitCreate">
          Открыть спор
        </BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, defineComponent, h } from 'vue'
import { useRouter } from 'vue-router'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import { disputesService } from '@/api/services/disputes.service'
import { useToast } from '@/composables/useToast'
import { formatDate } from '@/utils/format'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import DisputeEvidence from '@/components/disputes/DisputeEvidence.vue'
import type {
  DisputeEvidenceItem,
  DisputeReason,
  DisputeResponse,
  DisputeStatus,
  DisputeSubstatus,
  PaymentMethod,
} from '@/types'
import {
  disputeReasonLabels,
  disputeReasonOptions,
  disputeReasonOptionsWithAll,
  disputeStatusOptionsWithAll,
  disputeSubstatusLabels,
  paymentMethodOptionsWithAll,
} from '@/constants'

const DisputeStatusBadge = defineComponent({
  props: { status: String as () => DisputeStatus },
  setup(props) {
    const map: Record<string, { label: string; cls: string }> = {
      open:        { label: 'Открыт',      cls: 'bg-status-warning/15 text-status-warning border-status-warning/30' },
      resolved:    { label: 'Решён',       cls: 'bg-status-success/15 text-status-success border-status-success/30' },
      rejected:    { label: 'Отклонён',    cls: 'bg-status-danger/15 text-status-danger border-status-danger/30' },
    }
    return () => {
      const cfg = map[props.status ?? ''] ?? { label: props.status, cls: 'bg-bg-hover text-text-muted border-border' }
      return h('span', { class: `inline-flex items-center rounded-lg border px-2 py-0.5 text-xs font-semibold ${cfg.cls}` }, cfg.label)
    }
  },
})

const toast = useToast()
const router = useRouter()
const loading = ref(false)
const actionLoading = ref(false)

// ── Create dispute ──────────────────────────────────────────────────────
const createReasonOptions = disputeReasonOptions
const showCreate = ref(false)
const creating = ref(false)
const createForm = reactive<{ orderUuid: string; reason: DisputeReason; files: File[]; urlsText: string }>({
  orderUuid: '',
  reason: 'no_payment',
  files: [],
  urlsText: '',
})

function openCreate() {
  createForm.orderUuid = ''
  createForm.reason = 'no_payment'
  createForm.files = []
  createForm.urlsText = ''
  showCreate.value = true
}

function onCreateFiles(e: Event) {
  createForm.files = Array.from((e.target as HTMLInputElement).files ?? [])
}

async function submitCreate() {
  if (!createForm.orderUuid.trim()) return
  creating.value = true
  try {
    const evidenceUrls = createForm.urlsText
      .split('\n')
      .map(u => u.trim())
      .filter(Boolean)
    await disputesService.create({
      order_uuid: createForm.orderUuid.trim(),
      reason: createForm.reason,
      files: createForm.files,
      evidenceUrls,
    })
    toast.success('Спор открыт')
    showCreate.value = false
    await load()
  } catch (e: any) {
    toast.error(e?.response?.data?.error?.message || 'Не удалось открыть спор')
  } finally {
    creating.value = false
  }
}

const disputes = ref<DisputeResponse[]>([])
const page = ref(1)
const perPage = ref(25)
const totalPages = ref(1)

const filters = reactive({
  id_search: '',
  trader_login: '',
  merchant_login: '',
  status: '' as '' | DisputeStatus,
  reason: '' as '' | DisputeReason,
  payment_method: '' as '' | PaymentMethod,
  amount_from: '',
  amount_to: '',
})

const statusOptions = disputeStatusOptionsWithAll
const reasonOptions = disputeReasonOptionsWithAll
const methodOptions = paymentMethodOptionsWithAll

const showDispute = ref(false)
const activeDispute = ref<DisputeResponse | null>(null)

const evidence = ref<DisputeEvidenceItem[]>([])
const evidenceLoading = ref(false)
const adminEvidenceUrl = (item: DisputeEvidenceItem) =>
  disputesService.evidenceUrl(activeDispute.value!.id, item.uuid)

function substatusLabel(s: DisputeSubstatus) {
  return disputeSubstatusLabels[s] ?? s
}

const showResolution = ref(false)
const resolutionMode = ref<'resolve' | 'reject'>('resolve')
const resolutionText = ref('')

const columns: Column[] = [
  { key: 'uuid', label: 'UUID' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'dispute_status', label: 'Статус' },
  { key: 'merchant_login', label: 'Мерчант' },
  { key: 'trader_login', label: 'Трейдер' },
  { key: 'reason', label: 'Причина' },
  { key: 'created_at', label: 'Создан' },
]

const activeStatuses: DisputeStatus[] = ['open']

const sortedRows = computed(() => {
  const enriched = disputes.value.map(d => ({
    ...d,
    payment_method: d.order_payment_method ?? '—',
    order_amount: d.order_amount ?? null,
    order_currency: 'RUB',
    merchant_login: d.merchant_login ?? `#${d.merchant_id}`,
    trader_login: d.trader_login ?? '—',
    _isActive: activeStatuses.includes(d.status),
  }))
  return enriched.sort((a, b) => {
    if (a._isActive && !b._isActive) return -1
    if (!a._isActive && b._isActive) return 1
    return 0
  })
})

function formatReason(reason: string) {
  return disputeReasonLabels[reason as keyof typeof disputeReasonLabels] ?? reason
}

function applyFilters() {
  page.value = 1
  load()
}

function resetFilters() {
  filters.id_search = ''
  filters.trader_login = ''
  filters.merchant_login = ''
  filters.status = ''
  filters.reason = ''
  filters.payment_method = ''
  filters.amount_from = ''
  filters.amount_to = ''
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
    if (filters.id_search) params.id_search = filters.id_search.trim()
    if (filters.trader_login) params.trader_login = filters.trader_login.trim()
    if (filters.merchant_login) params.merchant_login = filters.merchant_login.trim()
    if (filters.status) params.status = filters.status
    if (filters.reason) params.reason = filters.reason
    if (filters.payment_method) params.payment_method = filters.payment_method
    if (filters.amount_from) params.amount_from = Number(filters.amount_from)
    if (filters.amount_to) params.amount_to = Number(filters.amount_to)

    const { data } = await disputesService.listAll(params)
    disputes.value = data
    totalPages.value = data.length < perPage.value ? page.value : page.value + 1
  } catch {
    toast.error('Ошибка загрузки споров')
  } finally {
    loading.value = false
  }
}

function openDispute(row: any) {
  activeDispute.value = disputes.value.find(d => d.id === row.id) ?? row
  showDispute.value = true
  loadEvidence()
}

async function loadEvidence() {
  if (!activeDispute.value) return
  evidence.value = []
  evidenceLoading.value = true
  try {
    const { data } = await disputesService.listEvidence(activeDispute.value.id)
    evidence.value = data
  } catch {
    // Evidence is best-effort context; a load failure shouldn't block the modal.
  } finally {
    evidenceLoading.value = false
  }
}

function viewDebug(dispute: DisputeResponse) {
  router.push(`/admin/orders?debug=${dispute.order_id}`)
}

function openResolveModal() {
  resolutionMode.value = 'resolve'
  resolutionText.value = ''
  showResolution.value = true
}

function openRejectModal() {
  resolutionMode.value = 'reject'
  resolutionText.value = ''
  showResolution.value = true
}

async function submitResolution() {
  if (!activeDispute.value || !resolutionText.value.trim()) return
  actionLoading.value = true
  try {
    const payload = { resolution_text: resolutionText.value.trim() }
    const { data } = resolutionMode.value === 'resolve'
      ? await disputesService.resolve(activeDispute.value.id, payload)
      : await disputesService.reject(activeDispute.value.id, payload)
    updateDisputeInList(data)
    showResolution.value = false
    toast.success(resolutionMode.value === 'resolve' ? 'Спор решён в пользу мерчанта' : 'Спор отклонён')
  } catch { toast.error('Ошибка при изменении спора') }
  finally { actionLoading.value = false }
}

function updateDisputeInList(updated: DisputeResponse) {
  const idx = disputes.value.findIndex(d => d.id === updated.id)
  if (idx !== -1) disputes.value[idx] = { ...disputes.value[idx], ...updated }
  activeDispute.value = { ...(activeDispute.value ?? {}), ...updated } as DisputeResponse
}

onMounted(load)
</script>
