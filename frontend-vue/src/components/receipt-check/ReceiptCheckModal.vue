<template>
  <BaseModal :model-value="modelValue" :title="title" size="md" @update:model-value="emit('update:modelValue', $event)">
    <div v-if="step === 'confirm'" class="space-y-4">
      <template v-if="alreadyChecked && existingCheck">
        <div class="flex items-center gap-2">
          <ReceiptCheckBadge :check="existingCheck" />
        </div>
        <p class="text-text-secondary">
          Нажмите, чтобы посмотреть результат проверки <span class="text-text-muted">(без списания)</span>.
        </p>
      </template>

      <template v-else>
        <p v-if="providersLoading" class="text-text-secondary">Загрузка провайдеров…</p>
        <p
          v-else-if="loadError"
          class="rounded-lg bg-status-danger/10 px-3 py-3 text-sm text-status-danger"
        >
          Не удалось загрузить провайдеров. Закройте и откройте окно, чтобы повторить.
        </p>
        <p
          v-else-if="providers.length === 0"
          class="rounded-lg bg-status-warning/10 px-3 py-3 text-sm text-status-warning"
        >
          Сервис проверки временно недоступен
        </p>
        <template v-else>
          <ReceiptProviderRadioGroup v-model="selectedProviderId" :providers="providers" />
        </template>
      </template>
    </div>

    <div v-else-if="step === 'running'" class="flex flex-col items-center gap-3 py-6">
      <Shield class="h-12 w-12 animate-pulse text-accent" />
      <p class="text-text-main">Отправляем чек на проверку…</p>
      <p class="text-xs text-text-muted">Это может занять до 90 секунд.</p>
    </div>

    <div v-else-if="step === 'result' && result" class="space-y-4">
      <ReceiptCheckResultBlock :check="result" />
    </div>

    <template #footer>
      <template v-if="step === 'confirm'">
        <BaseButton variant="dark" @click="close">Отмена</BaseButton>
        <BaseButton
          variant="gold"
          :loading="false"
          :disabled="!canRun"
          @click="run"
        >
          {{ alreadyChecked ? 'Показать результат' : 'Проверить' }}
        </BaseButton>
      </template>
      <template v-else-if="step === 'running'">
        <BaseButton variant="dark" disabled>Идёт проверка…</BaseButton>
      </template>
      <template v-else>
        <BaseButton variant="dark" @click="close">Закрыть</BaseButton>
      </template>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import { Shield } from 'lucide-vue-next'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import ReceiptCheckResultBlock from './ReceiptCheckResultBlock.vue'
import ReceiptCheckBadge from './ReceiptCheckBadge.vue'
import ReceiptProviderRadioGroup from './ReceiptProviderRadioGroup.vue'
import { receiptChecksService } from '@/api/services/receiptChecks.service'
import { tradersService } from '@/api/services/traders.service'
import { useToast } from '@/composables/useToast'
import type { ReceiptCheck, TraderReceiptProvider } from '@/types'

const props = defineProps<{
  modelValue: boolean
  orderUuid: string
  // Optional: pre-existing check used to show "already verified" state right away.
  existingCheck?: ReceiptCheck | null
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'completed', check: ReceiptCheck): void
}>()

const toast = useToast()

const step = ref<'confirm' | 'running' | 'result'>('confirm')
const providers = ref<TraderReceiptProvider[]>([])
const providersLoading = ref(false)
const loadError = ref(false)
const selectedProviderId = ref<number | null>(null)
const result = ref<ReceiptCheck | null>(null)

const alreadyChecked = computed(
  () => !!props.existingCheck && (props.existingCheck.status === 'success' || props.existingCheck.status === 'cached'),
)

// "Run" enabled when a provider is selected OR when we're just replaying an
// already-known verdict (no provider needed for the cached path).
const canRun = computed(
  () => alreadyChecked.value || (providers.value.length > 0 && selectedProviderId.value != null),
)

const title = computed(() => {
  if (step.value === 'result') return 'Результат проверки'
  if (step.value === 'running') return 'Проверка чека'
  return 'Проверить чек'
})

// `immediate: true` covers the first-open case in ActiveOrdersView, where
// the modal sits behind a `v-if="modalOrderUuid"`. On the first click the
// component mounts with `modelValue` already true, so a plain watch never
// fires (no value change) and the provider list stays empty.
watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return
    step.value = 'confirm'
    result.value = null
    providers.value = []
    selectedProviderId.value = null
    loadError.value = false
    providersLoading.value = true
    try {
      const { data } = await receiptChecksService.listProviders()
      providers.value = data
      // Seed from the trader's saved default; fall back to the first active
      // provider. Reading the profile is best-effort — a hiccup there must not
      // block checking.
      let defaultId: number | null = null
      try {
        const me = await tradersService.getMe()
        defaultId = me.data.default_receipt_check_provider_id ?? null
      } catch {
        // non-fatal — fall back to first active
      }
      const hasDefault = defaultId != null && providers.value.some((p) => p.id === defaultId)
      selectedProviderId.value = hasDefault ? defaultId : (providers.value[0]?.id ?? null)
    } catch (e: any) {
      loadError.value = true
      toast.error('Не удалось получить список провайдеров')
    } finally {
      providersLoading.value = false
    }
  },
  { immediate: true },
)

function close() {
  emit('update:modelValue', false)
}

async function run() {
  // If the file was already verified, skip the provider round-trip entirely
  // and just display the existing verdict — no charge, no network call, no
  // dependency on a provider being currently active.
  if (alreadyChecked.value && props.existingCheck) {
    result.value = props.existingCheck
    step.value = 'result'
    return
  }

  step.value = 'running'
  try {
    const { data } = await receiptChecksService.runManual(props.orderUuid, selectedProviderId.value)
    result.value = data
    step.value = 'result'
    if (data.status === 'cached') {
      toast.info('Показан результат предыдущей проверки')
    } else if (data.status === 'failed') {
      toast.error(
        data.refunded
          ? 'Ошибка проверки — списание возвращено на баланс'
          : 'Не удалось выполнить проверку',
      )
    } else if (data.is_clean) {
      toast.success('Чек чист')
    } else {
      toast.warning('Обнаружены признаки подделки')
    }
    emit('completed', data)
  } catch (e: any) {
    const msg = e?.response?.data?.detail || e?.response?.data?.message || 'Не удалось выполнить проверку'
    toast.error(typeof msg === 'string' ? msg : 'Не удалось выполнить проверку')
    step.value = 'confirm'
  }
}
</script>
