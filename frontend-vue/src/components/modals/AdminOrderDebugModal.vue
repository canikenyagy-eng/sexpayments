<template>
  <BaseModal :model-value="isOpen" :title="`Debug ордера #${localData?.order?.id ?? identifier}`" size="lg" @update:model-value="onModalUpdate">
    <div v-if="isLoading" class="py-8 text-center">
      <span class="inline-block h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
    </div>
    <div v-else-if="localData" class="space-y-6 max-h-[75vh] overflow-y-auto pr-1">
      <div>
        <h4 class="mb-2 text-sm font-bold text-accent">Ордер</h4>
        <div class="grid grid-cols-2 gap-2 text-sm">
          <div><span class="text-text-muted">UUID:</span> <UuidDisplay :value="localData.order.uuid" variant="icon" class="ml-1" /></div>
          <div><span class="text-text-muted">External ID:</span> <span class="text-text-main">{{ localData.order.external_id }}</span></div>
          <div><span class="text-text-muted">Merchant:</span> <span class="text-text-main">{{ localData.order.merchant_id }}</span></div>
          <div><span class="text-text-muted">Trader:</span> <span class="text-text-main">{{ localData.order.trader_id ?? '—' }}</span></div>
          <div><span class="text-text-muted">Метод:</span> <MethodBadge :method="localData.order.payment_method" /></div>
          <div><span class="text-text-muted">Статус:</span> <StatusBadge :status="localData.order.status" /></div>
          <div><span class="text-text-muted">Сумма:</span> <span class="text-text-main">{{ localData.order.amount }} {{ localData.order.currency }}</span></div>
          <div><span class="text-text-muted">USDT:</span> <span class="text-text-main">{{ localData.order.amount_usdt ?? '—' }}</span></div>
        </div>
      </div>

      <div>
        <h4 class="mb-2 text-sm font-bold text-accent">История статусов</h4>
        <div v-for="h in localData.status_history" :key="h.id" class="mb-1 flex items-center gap-2 text-xs text-text-secondary">
          <StatusBadge :status="h.new_status" />
          <span class="text-text-muted">{{ formatDate(h.created_at) }}</span>
          <span v-if="h.reason" class="text-text-muted">— {{ h.reason }}</span>
        </div>
        <p v-if="!localData.status_history.length" class="text-xs text-text-muted">Нет записей</p>
      </div>

      <div v-if="(localData.traders_candidates?.length ?? 0) > 0">
        <h4 class="mb-2 text-sm font-bold text-accent">Трейдеры (кандидаты)</h4>
        <div class="overflow-hidden rounded-lg border border-border">
          <table class="w-full text-xs">
            <thead class="bg-bg-surface/70 text-text-muted">
              <tr>
                <th class="px-3 py-2 text-left font-semibold">#</th>
                <th class="px-3 py-2 text-left font-semibold">Трейдер</th>
                <th class="px-3 py-2 text-left font-semibold">Статус</th>
                <th class="px-3 py-2 text-left font-semibold">PayIn / PayOut</th>
                <th class="px-3 py-2 text-left font-semibold">Метод</th>
                <th class="px-3 py-2 text-left font-semibold">Причина</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="(c, idx) in localData.traders_candidates"
                :key="(c.user_id ?? 'x') + '-' + (c.requisite_id ?? idx)"
                :class="c.is_selected ? 'bg-status-success/15 text-text-main' : 'text-text-secondary'"
              >
                <td class="px-3 py-2 font-mono text-[11px]">
                  <span v-if="c.is_selected" class="mr-1 font-bold text-status-success">★</span>
                  {{ c.user_id ?? '—' }}
                </td>
                <td class="px-3 py-2">{{ c.username ?? '—' }}</td>
                <td class="px-3 py-2">{{ c.status ?? '—' }}</td>
                <td class="px-3 py-2">{{ c.is_payin_active ? '✓' : '×' }} / {{ c.is_payout_active ? '✓' : '×' }}</td>
                <td class="px-3 py-2">
                  <template v-if="c.method_config">
                    fee {{ c.method_config.fee }}% · {{ c.method_config.min_amount }} – {{ c.method_config.max_amount }}
                  </template>
                  <template v-else>—</template>
                </td>
                <td class="px-3 py-2 text-status-danger">{{ c.reason ?? (c.is_excluded ? 'исключен' : '') }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div>
        <div class="mb-2 flex items-center justify-between">
          <h4 class="text-sm font-bold text-accent">API-логи мерчанта</h4>
          <span class="text-[11px] text-text-muted">всего: {{ localData.merchant_api_logs.length }}</span>
        </div>
        <div class="max-h-72 overflow-y-auto rounded-lg border border-border bg-bg-card p-2 text-xs">
          <div v-for="(log, i) in localData.merchant_api_logs" :key="log.request_id ?? i" class="mb-2 rounded-lg bg-bg-surface/70 p-2">
            <div class="flex flex-wrap items-center gap-2">
              <BaseBadge :color="log.response_status && log.response_status < 400 ? 'success' : 'danger'">{{ log.method }}</BaseBadge>
              <span class="truncate font-mono text-text-muted" :title="log.url" style="max-width: 320px">{{ log.url }}</span>
              <span>→ {{ log.response_status ?? '—' }}</span>
              <span v-if="log.response_time_ms != null" class="ml-auto text-text-muted">{{ log.response_time_ms }} мс</span>
            </div>
            <div v-if="log.request_body" class="mt-1 truncate font-mono text-[10px] text-text-muted" :title="log.request_body">
              → {{ truncate(log.request_body, 160) }}
            </div>
            <div v-if="log.response_body" class="truncate font-mono text-[10px] text-text-muted" :title="log.response_body">
              ← {{ truncate(log.response_body, 160) }}
            </div>
          </div>
          <p v-if="!localData.merchant_api_logs.length" class="text-text-muted">Нет логов</p>
        </div>
      </div>

      <div>
        <h4 class="mb-2 text-sm font-bold text-accent">Callback-попытки</h4>
        <div class="overflow-hidden rounded-lg border border-border">
          <table class="w-full text-xs">
            <thead class="bg-bg-surface/70 text-text-muted">
              <tr>
                <th class="px-3 py-2 text-left font-semibold">#</th>
                <th class="px-3 py-2 text-left font-semibold">URL</th>
                <th class="px-3 py-2 text-left font-semibold">HTTP</th>
                <th class="px-3 py-2 text-left font-semibold">Статус</th>
                <th class="px-3 py-2 text-left font-semibold">Время</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="cb in localData.callback_attempts" :key="cb.id" class="text-text-secondary">
                <td class="px-3 py-2 font-mono">{{ cb.attempt_number }}</td>
                <td class="px-3 py-2 truncate font-mono" :title="cb.url" style="max-width: 260px">{{ cb.url }}</td>
                <td class="px-3 py-2">{{ cb.response_status ?? '—' }}</td>
                <td class="px-3 py-2">
                  <BaseBadge :color="cb.is_successful ? 'success' : 'danger'">
                    {{ cb.is_successful ? 'OK' : 'FAIL' }}
                  </BaseBadge>
                </td>
                <td class="px-3 py-2 text-text-muted">{{ formatDate(cb.created_at) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="!localData.callback_attempts.length" class="px-3 py-2 text-xs text-text-muted">Нет попыток</p>
        </div>
      </div>

      <div>
        <h4 class="mb-2 text-sm font-bold text-accent">Записи в леджере</h4>
        <div class="overflow-hidden rounded-lg border border-border">
          <table class="w-full text-xs">
            <thead class="bg-bg-surface/70 text-text-muted">
              <tr>
                <th class="px-3 py-2 text-left font-semibold">Ref type</th>
                <th class="px-3 py-2 text-right font-semibold">Сумма</th>
                <th class="px-3 py-2 text-left font-semibold">From</th>
                <th class="px-3 py-2 text-left font-semibold">To</th>
                <th class="px-3 py-2 text-left font-semibold">Описание</th>
                <th class="px-3 py-2 text-left font-semibold">Время</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="le in localData.ledger_entries" :key="le.id" class="text-text-secondary">
                <td class="px-3 py-2">{{ le.reference_type }}</td>
                <td class="px-3 py-2 text-right font-mono">{{ le.amount }} {{ le.currency }}</td>
                <td class="px-3 py-2 font-mono">{{ le.from_balance_id ?? '—' }}</td>
                <td class="px-3 py-2 font-mono">{{ le.to_balance_id ?? '—' }}</td>
                <td class="px-3 py-2 truncate" :title="le.description ?? ''" style="max-width: 220px">{{ le.description ?? '' }}</td>
                <td class="px-3 py-2 text-text-muted">{{ formatDate(le.created_at) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-if="!localData.ledger_entries.length" class="px-3 py-2 text-xs text-text-muted">Нет записей</p>
        </div>
      </div>

      <div v-if="localData.dispute">
        <h4 class="mb-2 text-sm font-bold text-accent">Спор #{{ localData.dispute.id }}</h4>
        <div class="rounded-lg bg-bg-card p-2 text-xs">
          <span class="text-text-muted">Статус:</span> {{ localData.dispute.status }}
          <span class="text-text-muted ml-2">Причина:</span> {{ localData.dispute.reason }}
        </div>
      </div>

      <div v-if="localData.order.receipt_file">
        <h4 class="mb-2 text-sm font-bold text-accent">Чек</h4>
        <BaseButton variant="dark" size="sm" :loading="downloadingReceipt" @click="openReceipt(localData.order.uuid)">
          Открыть чек
        </BaseButton>
      </div>
    </div>
    <div v-else class="py-8 text-center text-status-danger">
      Не удалось загрузить данные
    </div>
    <template #footer>
      <BaseButton variant="dark" @click="close">Закрыть</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import { ordersService } from '@/api/services/orders.service'
import { useToast } from '@/composables/useToast'
import { formatDate, truncate } from '@/utils/format'
import { downloadReceipt, preOpenReceiptTab } from '@/utils/receipt'
import type { OrderDebug } from '@/types'

const toast = useToast()

const props = withDefaults(defineProps<{
  modelValue?: boolean
  data?: OrderDebug | null
  loading?: boolean
}>(), {
  modelValue: false,
  data: null,
  loading: false,
})

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const internalOpen = ref(false)
const internalData = ref<OrderDebug | null>(null)
const internalLoading = ref(false)
const downloadingReceipt = ref(false)
const identifier = ref('')

const isOpen = computed(() => props.modelValue || internalOpen.value)
const localData = computed(() => props.data ?? internalData.value)
const isLoading = computed(() => props.loading || internalLoading.value)

watch(() => props.modelValue, (val) => {
  if (!val) internalData.value = null
})

function onModalUpdate(value: boolean) {
  if (props.modelValue !== undefined) emit('update:modelValue', value)
  internalOpen.value = value
  if (!value) internalData.value = null
}

function close() {
  onModalUpdate(false)
}

async function open(id: string) {
  identifier.value = id
  internalOpen.value = true
  internalLoading.value = true
  internalData.value = null
  try {
    const { data } = await ordersService.debug(id)
    internalData.value = data
  } catch {
    toast.error('Ошибка загрузки debug-данных ордера')
  } finally {
    internalLoading.value = false
  }
}

function openReceipt(uuid: string) {
  // Pre-open the tab synchronously (the click handler IS a user gesture)
  // so mobile browsers don't block the post-await window.open.
  const tab = preOpenReceiptTab()
  downloadingReceipt.value = true
  downloadReceipt(`/api/v1/orders/${uuid}/receipt`, tab)
    .catch((e: any) => {
      toast.error(e?.message || 'Не удалось открыть чек')
    })
    .finally(() => {
      downloadingReceipt.value = false
    })
}


defineExpose({ open })
</script>
