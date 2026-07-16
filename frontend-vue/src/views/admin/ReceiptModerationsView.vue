<template>
  <div>
    <PageHeader title="Чеки" />

    <div class="mb-4 flex items-center gap-2">
      <BaseFilter :active-count="activeFilterCount" @apply="page = 1; load()" @reset="resetFilters">
        <BaseSelect v-model="filters.decision" label="Решение" :options="decisionOptions" />
        <BaseSelect v-model="filters.pending_only" label="Только ожидающие" :options="boolOptions" />
        <BaseInput v-model="filters.merchant_id" label="Merchant ID" placeholder="42" />
      </BaseFilter>
    </div>

    <DataTable
      :columns="columns"
      :rows="displayRows"
      :loading="loading"
      row-key="id"
      :current-page="page"
      :total-pages="totalPages"
      :per-page="perPage"
      @page-change="p => { page = p; load() }"
      @per-page-change="n => { perPage = n; page = 1; load() }"
    >
      <template #cell-order_external_id="{ value }">
        <UuidDisplay :value="value as string" success-message="External ID скопирован" />
      </template>

      <template #cell-order_uuid="{ value }">
        <UuidDisplay :value="value as string" />
      </template>

      <template #cell-decision="{ row }">
        <BaseBadge :color="decisionColor((row as ReceiptModerationItem).decision)">
          {{ decisionLabel((row as ReceiptModerationItem).decision) }}
        </BaseBadge>
      </template>

      <template #cell-moderation_status="{ value }">
        <BaseBadge :color="statusColor(value as ModerationStatus)">
          {{ statusLabel(value as ModerationStatus) }}
        </BaseBadge>
      </template>

      <template #cell-moderator_username="{ row }">
        <span class="text-sm">
          {{ (row as ReceiptModerationItem).moderator_username || '—' }}
          <span
            v-if="(row as ReceiptModerationItem).moderator_tg_id"
            class="ml-1 font-mono text-xs text-text-muted"
          >
            #{{ (row as ReceiptModerationItem).moderator_tg_id }}
          </span>
        </span>
      </template>

      <template #cell-created_at="{ value }">
        {{ formatDate(value) }}
      </template>

      <template #cell-decided_at="{ value }">
        <span v-if="value">{{ formatDate(value) }}</span>
        <span v-else class="text-text-muted">—</span>
      </template>

      <template #cell-duration="{ row }">
        <span
          v-if="(row as any)._dur_label"
          class="whitespace-nowrap font-mono text-xs font-semibold"
          :class="(row as any)._dur_cls"
        >
          {{ (row as any)._dur_label }}
        </span>
        <span v-else class="text-text-muted">—</span>
      </template>

      <template #cell-merchant="{ row }">
        <span class="text-sm">
          {{ (row as ReceiptModerationItem).merchant_name || `#${(row as ReceiptModerationItem).merchant_id}` }}
        </span>
      </template>

      <template #cell-trader="{ row }">
        <span v-if="(row as ReceiptModerationItem).trader_username" class="text-sm">
          {{ (row as ReceiptModerationItem).trader_username }}
        </span>
        <span v-else-if="(row as ReceiptModerationItem).trader_id" class="text-sm text-text-muted">
          #{{ (row as ReceiptModerationItem).trader_id }}
        </span>
        <span v-else class="text-text-muted">—</span>
      </template>

      <template #cell-receipt="{ row }">
        <BaseButton
          v-if="(row as ReceiptModerationItem).has_receipt"
          variant="dark"
          size="sm"
          title="Открыть чек"
          @click.stop="openReceipt((row as ReceiptModerationItem).order_uuid)"
        >
          Чек
        </BaseButton>
        <span v-else class="text-text-muted">—</span>
      </template>

      <template #actions="{ row }">
        <div
          v-if="(row as ReceiptModerationItem).moderation_status === 'pending'"
          class="flex items-center justify-end gap-1.5"
        >
          <BaseButton
            variant="success"
            size="sm"
            :disabled="acting === (row as ReceiptModerationItem).order_id"
            :loading="acting === (row as ReceiptModerationItem).order_id && actingDecision === 'accept'"
            @click.stop="decide(row as ReceiptModerationItem, 'accept')"
          >
            Принять
          </BaseButton>
          <BaseButton
            variant="dark"
            size="sm"
            title="Запросить чек ПДФ"
            :disabled="acting === (row as ReceiptModerationItem).order_id"
            :loading="acting === (row as ReceiptModerationItem).order_id && actingDecision === 'request_pdf'"
            @click.stop="decide(row as ReceiptModerationItem, 'request_pdf')"
          >
            ПДФ
          </BaseButton>
          <BaseButton
            variant="dark"
            size="sm"
            title="Запросить Видео"
            :disabled="acting === (row as ReceiptModerationItem).order_id"
            :loading="acting === (row as ReceiptModerationItem).order_id && actingDecision === 'request_video'"
            @click.stop="decide(row as ReceiptModerationItem, 'request_video')"
          >
            Видео
          </BaseButton>
        </div>
        <span v-else class="text-text-muted">—</span>
      </template>
    </DataTable>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseFilter from '@/components/ui/BaseFilter.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { receiptModerationsService } from '@/api/services/receiptModerations.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { formatDate } from '@/utils/format'
import { downloadReceipt, preOpenReceiptTab } from '@/utils/receipt'
import type {
  ModerationDecision,
  ModerationStatus,
  ReceiptModerationItem,
  ReceiptModerationListParams,
} from '@/types'

const toast = useToast()
const { confirm: askConfirm } = useConfirm()

const loading = ref(false)
const items = ref<ReceiptModerationItem[]>([])
const total = ref(0)
const page = ref(1)
const perPage = ref(50)

const acting = ref<number | null>(null)
const actingDecision = ref<ModerationDecision | null>(null)

const DECISION_ACTION_LABELS: Record<ModerationDecision, string> = {
  accept: 'Принять',
  request_pdf: 'Запросить ПДФ',
  request_video: 'Запросить Видео',
}

const filters = reactive({
  decision: '' as '' | ModerationDecision,
  pending_only: '' as '' | 'true' | 'false',
  merchant_id: '',
})

const decisionOptions = [
  { value: '', label: 'Все' },
  { value: 'accept', label: 'Принято' },
  { value: 'request_pdf', label: 'Запрошен PDF' },
  { value: 'request_video', label: 'Запрошено видео' },
]

const boolOptions = [
  { value: '', label: 'Не важно' },
  { value: 'true', label: 'Да' },
  { value: 'false', label: 'Нет' },
]

const activeFilterCount = computed(
  () => [filters.decision, filters.pending_only, filters.merchant_id].filter(Boolean).length,
)

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / perPage.value)))

const columns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'order_external_id', label: 'External ID' },
  { key: 'order_uuid', label: 'Order UUID' },
  { key: 'merchant', label: 'Мерчант' },
  { key: 'trader', label: 'Трейдер' },
  { key: 'moderation_status', label: 'Статус' },
  { key: 'decision', label: 'Решение' },
  { key: 'receipt', label: 'Чек' },
  { key: 'moderator_username', label: 'Модератор' },
  { key: 'created_at', label: 'Создано' },
  { key: 'decided_at', label: 'Решено' },
  { key: 'duration', label: 'Время' },
]

// Time spent on moderation = decided_at − created_at. Both come as UTC-naive
// strings; we only take a delta so timezone parsing cancels out. Colour-coded:
// 0–2 мин green, 2–5 мин amber, 5+ мин red.
function _durationSeconds(row: ReceiptModerationItem): number | null {
  if (!row.created_at || !row.decided_at) return null
  const start = new Date(row.created_at).getTime()
  const end = new Date(row.decided_at).getTime()
  if (Number.isNaN(start) || Number.isNaN(end)) return null
  return Math.max(0, (end - start) / 1000)
}

function _durationLabel(sec: number): string {
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return m === 0 ? `${s} сек` : `${m} мин ${s} сек`
}

function _durationClass(sec: number): string {
  if (sec <= 120) return 'text-status-success'
  if (sec <= 300) return 'text-status-warning'
  return 'text-status-danger'
}

const displayRows = computed(() =>
  items.value.map((it) => {
    const sec = _durationSeconds(it)
    return {
      ...it,
      _dur_label: sec === null ? null : _durationLabel(sec),
      _dur_cls: sec === null ? '' : _durationClass(sec),
    }
  }),
)

function openReceipt(orderUuid: string) {
  // iOS Safari (and most mobile browsers) blocks window.open() once an
  // ``await`` boundary breaks the user-gesture chain. Open the tab
  // synchronously here on the click, then let downloadReceipt navigate it
  // once the authenticated fetch resolves. Same pattern as trader's
  // ActiveOrdersView.
  const tab = preOpenReceiptTab()
  downloadReceipt(`/api/v1/orders/${orderUuid}/receipt`, tab).catch((e: any) => {
    toast.error(e?.message || 'Не удалось открыть чек')
  })
}

type BadgeColor = 'success' | 'danger' | 'warning' | 'info' | 'default' | 'gold'

function decisionLabel(d?: ModerationDecision | null): string {
  if (!d) return 'Ожидает'
  if (d === 'accept') return 'Принято'
  if (d === 'request_pdf') return 'Запрос PDF'
  return 'Запрос видео'
}

function decisionColor(d?: ModerationDecision | null): BadgeColor {
  if (!d) return 'warning'
  if (d === 'accept') return 'success'
  return 'info'
}

const STATUS_LABELS: Record<ModerationStatus, string> = {
  none: '—',
  pending: 'В ожидании',
  approved: 'Одобрено',
  pdf_requested: 'PDF запрошен',
  video_requested: 'Видео запрошено',
}

const STATUS_COLORS: Record<ModerationStatus, BadgeColor> = {
  none: 'default',
  pending: 'warning',
  approved: 'success',
  pdf_requested: 'info',
  video_requested: 'info',
}

function statusLabel(s: ModerationStatus): string {
  return STATUS_LABELS[s]
}

function statusColor(s: ModerationStatus): BadgeColor {
  return STATUS_COLORS[s]
}

async function decide(row: ReceiptModerationItem, decision: ModerationDecision) {
  const label = DECISION_ACTION_LABELS[decision]
  const ok = await askConfirm({
    title: label,
    message: `Заявка ${row.order_external_id || row.order_uuid}: «${label}»? `
      + 'Действие повторяет кнопку в support-боте.',
    confirmText: label,
    cancelText: 'Отмена',
    variant: decision === 'accept' ? 'success' : 'gold',
  })
  if (!ok) return
  acting.value = row.order_id
  actingDecision.value = decision
  try {
    await receiptModerationsService.applyDecision(row.order_id, decision)
    toast.success('Решение применено')
    load()
  } catch (e: any) {
    const msg = e?.response?.data?.error?.message || e?.response?.data?.message
    toast.error(msg || 'Не удалось применить решение')
  } finally {
    acting.value = null
    actingDecision.value = null
  }
}

function resetFilters() {
  page.value = 1
  filters.decision = ''
  filters.pending_only = ''
  filters.merchant_id = ''
  load()
}

async function load() {
  loading.value = true
  try {
    const params: ReceiptModerationListParams = {
      skip: (page.value - 1) * perPage.value,
      limit: perPage.value,
    }
    if (filters.decision) params.decision = filters.decision as ModerationDecision
    if (filters.pending_only) params.pending_only = filters.pending_only === 'true'
    if (filters.merchant_id) params.merchant_id = Number(filters.merchant_id)

    const { data } = await receiptModerationsService.list(params)
    items.value = data.items
    total.value = data.total
  } catch {
    toast.error('Ошибка загрузки истории модерации')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
