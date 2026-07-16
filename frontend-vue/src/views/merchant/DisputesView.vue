<template>
  <div>
    <PageHeader title="Споры">
      <template #actions>
        <BaseButton variant="gold" size="sm" @click="openCreate">Открыть спор</BaseButton>
      </template>
    </PageHeader>

    <DataTable
      :columns="columns"
      :rows="disputes"
      :loading="loading"
      row-key="uuid"
      :current-page="page"
      :total-pages="totalPages"
      clickable
      @page-change="p => { page = p; load() }"
      @row-click="openDetails"
    >
      <template #cell-uuid="{ row }">
        <UuidDisplay :value="(row as any).uuid" show-icon />
      </template>
      <template #cell-order_external_id="{ row }">
        <button
          v-if="(row as any).order_external_id"
          type="button"
          class="inline-flex items-center gap-1 font-mono text-xs text-text-main hover:text-accent"
          @click.stop="copy(String((row as any).order_external_id))"
        >
          {{ (row as any).order_external_id }}
          <Copy class="h-3 w-3 opacity-70" />
        </button>
        <span v-else class="text-text-muted">—</span>
      </template>
      <template #cell-status="{ value }">
        <StatusBadge :status="value" />
      </template>
      <template #cell-reason="{ value }">
        <BaseBadge color="warning">{{ reasonLabel(value) }}</BaseBadge>
      </template>
      <template #cell-created_at="{ value }">
        {{ formatDate(value) }}
      </template>
    </DataTable>

    <BaseModal v-model="showDetails" :title="`Спор: ${detail?.uuid ? String(detail.uuid).slice(0, 8) : ''}`" size="lg">
      <div v-if="detailLoading" class="py-8 text-center"><LoadingSpinner /></div>
      <div v-else-if="detail" class="space-y-5 max-h-[70vh] overflow-y-auto">
        <div class="grid grid-cols-2 gap-2 text-sm">
          <div>
            <span class="text-text-muted">Ордер (UUID):</span>
            <UuidDisplay :value="detail.order_uuid" :truncate="false" show-icon class="ml-1" />
          </div>
          <div>
            <span class="text-text-muted">External ID:</span>
            <button
              v-if="detail.order_external_id"
              type="button"
              class="ml-2 inline-flex items-center gap-1 font-mono text-xs text-text-main hover:text-accent"
              @click="copy(String(detail.order_external_id))"
            >
              {{ detail.order_external_id }}
              <Copy class="h-3 w-3 opacity-70" />
            </button>
            <span v-else class="ml-2 text-text-muted">—</span>
          </div>
          <div><span class="text-text-muted">Статус:</span> <StatusBadge class="ml-2" :status="detail.status" /></div>
          <div><span class="text-text-muted">Причина:</span> <BaseBadge class="ml-2" color="warning">{{ reasonLabel(detail.reason) }}</BaseBadge></div>
          <div v-if="detail.substatus" class="col-span-2">
            <span class="text-text-muted">Премодерация:</span>
            <BaseBadge class="ml-2" color="warning">{{ substatusLabel(detail.substatus) }}</BaseBadge>
          </div>
          <div><span class="text-text-muted">Инициатор:</span> <span class="ml-2 text-text-main">{{ detail.initiator_type }}</span></div>
          <div v-if="detail.resolution_text" class="col-span-2">
            <span class="text-text-muted">Решение:</span>
            <p class="mt-1 text-text-main">{{ detail.resolution_text }}</p>
          </div>
        </div>

        <DisputeEvidence
          :items="evidence"
          :loading="evidenceLoading"
          :resolve-url="merchantEvidenceUrl"
        />
      </div>
    </BaseModal>

    <!-- Create dispute -->
    <BaseModal v-model="showCreate" title="Открыть спор" size="lg">
      <div class="space-y-4">
        <BaseSelect
          v-model="createForm.orderUuid"
          label="Ордер"
          :options="orderOptions"
          :placeholder="ordersLoading ? 'Загрузка ордеров…' : 'Выберите ордер'"
        />
        <BaseSelect v-model="createForm.reason" label="Причина" :options="disputeReasonOptions" />
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
        <BaseButton variant="gold" :loading="creating" :disabled="!createForm.orderUuid" @click="submitCreate">
          Открыть спор
        </BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import { Copy } from 'lucide-vue-next'
import { merchantsService } from '@/api/services/merchants.service'
import { useToast } from '@/composables/useToast'
import { copyToClipboard, formatDate } from '@/utils/format'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import DisputeEvidence from '@/components/disputes/DisputeEvidence.vue'
import { disputeReasonLabels, disputeReasonOptions, disputeSubstatusLabels } from '@/constants'
import type { DisputeEvidenceItem, DisputeReason, DisputeResponse, DisputeSubstatus, Order } from '@/types'

const toast = useToast()
const PER_PAGE = 25

const loading = ref(false)
const detailLoading = ref(false)
const disputes = ref<DisputeResponse[]>([])
const page = ref(1)
const totalPages = ref(1)

const showDetails = ref(false)
const detail = ref<DisputeResponse | null>(null)

const evidence = ref<DisputeEvidenceItem[]>([])
const evidenceLoading = ref(false)
const merchantEvidenceUrl = (item: DisputeEvidenceItem) =>
  merchantsService.disputeEvidenceUrl(String(detail.value!.uuid), item.uuid)

function substatusLabel(s: DisputeSubstatus) {
  return disputeSubstatusLabels[s] ?? s
}

const columns: Column[] = [
  { key: 'uuid', label: 'UUID' },
  { key: 'order_external_id', label: 'External ID' },
  { key: 'reason', label: 'Причина' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Дата' },
]

function reasonLabel(reason: string): string {
  return disputeReasonLabels[reason as keyof typeof disputeReasonLabels] ?? reason
}

async function copy(text: string) {
  try {
    await copyToClipboard(text)
    toast.success('Скопировано')
  } catch { /* ignore */ }
}

async function load() {
  loading.value = true
  try {
    const { data } = await merchantsService.listMyDisputes({
      skip: (page.value - 1) * PER_PAGE,
      limit: PER_PAGE,
    })
    disputes.value = data
    totalPages.value = data.length < PER_PAGE ? page.value : page.value + 1
  } catch {
    toast.error('Ошибка загрузки споров')
  } finally {
    loading.value = false
  }
}

// ── Create dispute ──────────────────────────────────────────────────────
const showCreate = ref(false)
const creating = ref(false)
const ordersLoading = ref(false)
const orders = ref<Order[]>([])
const createForm = reactive<{ orderUuid: string; reason: DisputeReason; files: File[]; urlsText: string }>({
  orderUuid: '',
  reason: 'no_payment',
  files: [],
  urlsText: '',
})

const orderOptions = computed(() =>
  orders.value.map(o => ({
    value: o.uuid,
    label: `${o.external_id || String(o.uuid).slice(0, 8)} · ${o.amount} ${o.currency} · ${o.status}`,
  })),
)

async function loadOrders() {
  ordersLoading.value = true
  try {
    const { data } = await merchantsService.listMyOrders({ limit: 100 })
    // A dispute can't be opened on an already-disputed order.
    orders.value = data.items.filter(o => o.status !== 'disputed')
  } catch {
    toast.error('Не удалось загрузить ордера')
  } finally {
    ordersLoading.value = false
  }
}

function openCreate() {
  createForm.orderUuid = ''
  createForm.reason = 'no_payment'
  createForm.files = []
  createForm.urlsText = ''
  showCreate.value = true
  loadOrders()
}

function onCreateFiles(e: Event) {
  createForm.files = Array.from((e.target as HTMLInputElement).files ?? [])
}

async function submitCreate() {
  if (!createForm.orderUuid) return
  creating.value = true
  try {
    const evidenceUrls = createForm.urlsText
      .split('\n')
      .map(u => u.trim())
      .filter(Boolean)
    await merchantsService.openDispute(createForm.orderUuid, {
      reason: createForm.reason,
      files: createForm.files,
      evidenceUrls,
    })
    toast.success('Спор открыт')
    showCreate.value = false
    page.value = 1
    await load()
  } catch (e: any) {
    toast.error(e?.response?.data?.error?.message || 'Не удалось открыть спор')
  } finally {
    creating.value = false
  }
}

async function openDetails(row: Record<string, any>) {
  const d = row as DisputeResponse
  showDetails.value = true
  detailLoading.value = true
  evidence.value = []
  try {
    const { data } = await merchantsService.getMyDispute(String(d.uuid))
    detail.value = data
    loadEvidence(String(d.uuid))
  } catch {
    toast.error('Ошибка загрузки спора')
  } finally {
    detailLoading.value = false
  }
}

async function loadEvidence(uuid: string) {
  evidenceLoading.value = true
  try {
    const { data } = await merchantsService.listMyDisputeEvidence(uuid)
    evidence.value = data
  } catch {
    // Best-effort context — don't block the detail modal on it.
  } finally {
    evidenceLoading.value = false
  }
}

onMounted(load)
</script>
