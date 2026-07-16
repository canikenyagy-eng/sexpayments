<template>
  <div>
    <PageHeader title="Споры" />

    <div class="mb-4 flex flex-wrap items-center gap-2">
      <BaseInput
        v-model="filters.id_search"
        placeholder="Поиск по UUID / external ID"
        class="min-w-[280px] flex-1"
        @keyup.enter="page = 1; load()"
      />
      <BaseFilter :active-count="activeFilterCount" @apply="page = 1; load()" @reset="resetFilters">
        <BaseSelect v-model="filters.status" label="Статус" :options="statusOptions" />
        <BaseSelect v-model="filters.reason" label="Причина" :options="reasonOptions" />
        <BaseSelect v-model="filters.payment_method" label="Метод" :options="methodOptions" />
        <div class="grid grid-cols-2 gap-2">
          <BaseInput v-model="filters.amount_from" label="Сумма от" type="number" placeholder="0" />
          <BaseInput v-model="filters.amount_to" label="Сумма до" type="number" placeholder="∞" />
        </div>
      </BaseFilter>
    </div>

    <DataTable
      :columns="columns"
      :rows="disputes"
      :loading="loading"
      row-key="id"
      :current-page="page"
      :total-pages="totalPages"
      clickable
      @page-change="p => { page = p; load() }"
      @row-click="row => openDetail(row as DisputeResponse)"
    >
      <template #cell-uuid="{ row }">
        <UuidDisplay :value="(row as any).uuid" show-icon />
      </template>
      <template #cell-amount="{ row }">
        <span class="font-bold text-text-main">{{ (row as any).order_amount != null ? formatAmount((row as any).order_amount) : '—' }}</span>
      </template>
      <template #cell-order_payment_method="{ value }">
        <MethodBadge v-if="value" :method="value" />
        <span v-else class="text-text-muted">—</span>
      </template>
      <template #cell-reason="{ value }">
        <BaseBadge color="warning">{{ reasonLabel(value) }}</BaseBadge>
      </template>
      <template #cell-status="{ value }">
        <StatusBadge :status="value" />
      </template>
      <template #cell-created_at="{ value }">
        {{ value ? formatDate(value) : '—' }}
      </template>
    </DataTable>

    <BaseModal v-model="showDetail" :title="detailTitle" size="lg">
      <div v-if="detail" class="space-y-5 max-h-[70vh] overflow-y-auto">
        <div class="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span class="text-text-muted">UUID спора:</span>
            <UuidDisplay :value="detail.uuid" :truncate="false" show-icon class="ml-1" />
          </div>
          <div><span class="text-text-muted">Статус:</span> <StatusBadge class="ml-2" :status="detail.status" /></div>
          <div><span class="text-text-muted">Причина:</span> <BaseBadge class="ml-2" color="warning">{{ reasonLabel(detail.reason) }}</BaseBadge></div>
          <div v-if="detail.substatus" class="col-span-2">
            <span class="text-text-muted">Премодерация:</span>
            <BaseBadge class="ml-2" color="warning">{{ substatusLabel(detail.substatus) }}</BaseBadge>
          </div>
          <div>
            <span class="text-text-muted">Метод:</span>
            <MethodBadge v-if="detail.order_payment_method" class="ml-2" :method="detail.order_payment_method" />
            <span v-else class="ml-2 text-text-muted">—</span>
          </div>
          <div>
            <span class="text-text-muted">Сумма:</span>
            <strong class="ml-2 text-text-main">{{ detail.order_amount != null ? formatAmount(detail.order_amount) : '—' }}</strong>
          </div>
          <div v-if="detail.resolved_at" class="col-span-2">
            <span class="text-text-muted">Решение:</span>
            <p class="mt-1 text-text-main">{{ detail.resolution_text || '—' }}</p>
          </div>
        </div>

        <DisputeEvidence
          :items="evidence"
          :loading="evidenceLoading"
          :resolve-url="traderEvidenceUrl"
          :show-source="false"
        />

        <!-- Действия трейдера по ОТКРЫТОМУ спору -->
        <div v-if="detail.status === 'open'" class="border-t border-border pt-4">
          <div class="flex flex-wrap gap-2">
            <BaseButton variant="success" size="sm" :loading="acting" @click="accept">
              Принять
            </BaseButton>
            <BaseButton variant="danger" size="sm" :loading="acting" @click="reject">
              Отклонить
            </BaseButton>
            <BaseButton variant="dark" size="sm" :loading="acting" @click="requestProof('video')">
              Запросить Видео
            </BaseButton>
            <BaseButton variant="dark" size="sm" :loading="acting" @click="requestProof('pdf')">
              Запросить ПДФ
            </BaseButton>
          </div>
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
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseFilter from '@/components/ui/BaseFilter.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import { disputesService } from '@/api/services/disputes.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { formatAmount, formatDate } from '@/utils/format'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import DisputeEvidence from '@/components/disputes/DisputeEvidence.vue'
import {
  disputeReasonLabels,
  disputeReasonOptionsWithAll,
  disputeStatusOptionsWithAll,
  disputeSubstatusLabels,
  paymentMethodOptionsWithAll,
} from '@/constants'
import type { DisputeEvidenceItem, DisputeResponse, DisputeStatus, DisputeReason, DisputeSubstatus, PaymentMethod } from '@/types'

const toast = useToast()
const { confirm } = useConfirm()
const PER_PAGE = 25

const loading = ref(true)
const disputes = ref<DisputeResponse[]>([])
const page = ref(1)
const totalPages = ref(1)

const showDetail = ref(false)
const detail = ref<DisputeResponse | null>(null)
const acting = ref(false)

const evidence = ref<DisputeEvidenceItem[]>([])
const evidenceLoading = ref(false)
const traderEvidenceUrl = (item: DisputeEvidenceItem) =>
  disputesService.myEvidenceUrl(String(detail.value!.uuid), item.uuid)

function substatusLabel(s: DisputeSubstatus) {
  return disputeSubstatusLabels[s] ?? s
}

const detailTitle = computed(() => detail.value?.uuid ? `Спор: ${String(detail.value.uuid).slice(0, 8)}` : 'Спор')

interface Filters {
  id_search: string
  status: DisputeStatus | ''
  reason: DisputeReason | ''
  payment_method: PaymentMethod | ''
  amount_from: string
  amount_to: string
}

const filters = reactive<Filters>({
  id_search: '',
  status: '',
  reason: '',
  payment_method: '',
  amount_from: '',
  amount_to: '',
})

const statusOptions = disputeStatusOptionsWithAll
const reasonOptions = disputeReasonOptionsWithAll
const methodOptions = paymentMethodOptionsWithAll

const columns: Column[] = [
  { key: 'uuid', label: 'UUID' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'order_payment_method', label: 'Метод' },
  { key: 'reason', label: 'Причина' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Создан' },
]

const activeFilterCount = computed(() => {
  let n = 0
  if (filters.status) n++
  if (filters.reason) n++
  if (filters.payment_method) n++
  if (filters.amount_from) n++
  if (filters.amount_to) n++
  return n
})

function reasonLabel(reason: string): string {
  return disputeReasonLabels[reason as keyof typeof disputeReasonLabels] ?? reason
}

function resetFilters() {
  filters.id_search = ''
  filters.status = ''
  filters.reason = ''
  filters.payment_method = ''
  filters.amount_from = ''
  filters.amount_to = ''
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = { skip: (page.value - 1) * PER_PAGE, limit: PER_PAGE }
    if (filters.id_search.trim()) params.id_search = filters.id_search.trim()
    if (filters.status) params.status = filters.status
    if (filters.reason) params.reason = filters.reason
    if (filters.payment_method) params.payment_method = filters.payment_method
    if (filters.amount_from) params.amount_from = Number(filters.amount_from)
    if (filters.amount_to) params.amount_to = Number(filters.amount_to)
    const { data } = await disputesService.listMy(params)
    disputes.value = data
    totalPages.value = data.length < PER_PAGE ? page.value : page.value + 1
  } catch { toast.error('Ошибка загрузки споров') }
  finally { loading.value = false }
}

async function openDetail(row: DisputeResponse) {
  try {
    const { data } = await disputesService.getMy(String(row.uuid))
    detail.value = data
    showDetail.value = true
    loadEvidence(String(row.uuid))
  } catch { toast.error('Ошибка загрузки деталей') }
}

async function loadEvidence(uuid: string) {
  evidence.value = []
  evidenceLoading.value = true
  try {
    const { data } = await disputesService.listMyEvidence(uuid)
    evidence.value = data
  } catch {
    // Best-effort context — don't block the detail modal on it.
  } finally {
    evidenceLoading.value = false
  }
}

async function accept() {
  if (!detail.value || acting.value) return
  const ok = await confirm({
    title: 'Принять спор?',
    message: 'Заявка станет успешной, средства уйдут мерчанту. Действие необратимо.',
    variant: 'success',
    confirmText: 'Принять',
  })
  if (!ok) return
  acting.value = true
  try {
    const { data } = await disputesService.acceptMy(String(detail.value.uuid))
    detail.value = data
    toast.success('Спор принят')
    await load()
  } catch { toast.error('Не удалось принять спор') }
  finally { acting.value = false }
}

async function reject() {
  if (!detail.value || acting.value) return
  const ok = await confirm({
    title: 'Отклонить спор?',
    message: 'Заявка станет неуспешной, замороженные средства вернутся вам. Действие необратимо.',
    variant: 'danger',
    confirmText: 'Отклонить',
  })
  if (!ok) return
  acting.value = true
  try {
    const { data } = await disputesService.rejectMy(String(detail.value.uuid))
    detail.value = data
    toast.success('Спор отклонён')
    await load()
  } catch { toast.error('Не удалось отклонить спор') }
  finally { acting.value = false }
}

async function requestProof(kind: 'video' | 'pdf') {
  if (!detail.value || acting.value) return
  const label = kind === 'video' ? 'видео' : 'ПДФ'
  const ok = await confirm({
    title: `Запросить ${label}?`,
    message: `Мерчанту уйдёт запрос на ${label}. Спор останется открытым до получения нового доказательства.`,
    variant: 'gold',
    confirmText: 'Запросить',
  })
  if (!ok) return
  acting.value = true
  try {
    const { data } = await disputesService.requestProofMy(String(detail.value.uuid), kind)
    detail.value = data
    toast.success(`Запрошено ${label} — ожидаем доказательство от мерчанта`)
    await load()
  } catch { toast.error(`Не удалось запросить ${label}`) }
  finally { acting.value = false }
}

onMounted(load)
</script>
