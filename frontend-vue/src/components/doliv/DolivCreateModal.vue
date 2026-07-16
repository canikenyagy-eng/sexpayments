<template>
  <BaseModal :model-value="modelValue" title="Запросить долив" @update:model-value="$emit('update:modelValue', $event)">
    <div class="space-y-4">
      <div class="rounded-xl border border-border bg-bg-card p-3 text-sm">
        <div class="flex items-center justify-between">
          <span class="text-text-muted">Банк</span>
          <span class="flex items-center gap-1.5">
            <PaymentOptionLogo :src="requisite?.logo_url ?? null" :alt="bankLabel" :size="16" />
            <span class="text-text-main">{{ bankLabel }}</span>
          </span>
        </div>
        <div class="mt-1 flex justify-between">
          <span class="text-text-muted">Реквизит</span>
          <span class="font-mono text-text-main">{{ requisite?.account_number ?? '—' }}</span>
        </div>
        <div class="mt-1 flex justify-between">
          <span class="text-text-muted">Валюта</span>
          <span class="text-text-main">{{ requisite?.currency ?? '—' }}</span>
        </div>
        <div v-if="requisite?.remaining != null" class="mt-1 flex justify-between">
          <span class="text-text-muted">До лимита реквизита</span>
          <span class="text-text-main">{{ formatAmount(requisite.remaining) }} {{ requisite?.currency }}</span>
        </div>
        <div class="mt-1 flex justify-between">
          <span class="text-text-muted">Доступный долив</span>
          <span class="text-text-main">{{ rangeLabel }}</span>
        </div>
        <div class="mt-1 flex justify-between">
          <span class="text-text-muted">Стоимость</span>
          <span class="text-text-main">{{ pricePercent != null ? pricePercent + ' %' : '—' }}</span>
        </div>
      </div>

      <div class="space-y-1">
        <BaseInput v-model.number="amount" label="Сумма долива" type="number" min="0" />
        <p v-if="outOfRange" class="text-xs text-status-danger">
          Сумма вне допустимого размера долива ({{ rangeLabel }}).
        </p>
      </div>
    </div>
    <template #footer>
      <BaseButton variant="dark" @click="$emit('update:modelValue', false)">Отмена</BaseButton>
      <BaseButton variant="gold" :loading="busy" :disabled="!canSubmit" @click="submit">
        Запросить
      </BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import PaymentOptionLogo from '@/components/ui/PaymentOptionLogo.vue'
import { dolivService } from '@/api/services/doliv.service'
import { useToast } from '@/composables/useToast'
import { formatAmount } from '@/utils/format'
import type { Doliv } from '@/types'

const props = defineProps<{
  modelValue: boolean
  // ``remaining`` — fiat left until the requisite's daily limit (drives the range hint).
  requisite: {
    id: number
    currency: string
    account_number?: string
    remaining?: number | null
    bank_name?: string | null
    payment_option_name?: string | null
    logo_url?: string | null
    payment_method?: string
  } | null
}>()
const emit = defineEmits<{ 'update:modelValue': [boolean]; created: [Doliv] }>()

const toast = useToast()
const amount = ref<number | undefined>(undefined)
const busy = ref(false)
const dolivMin = ref<number>(0)            // долив min-amount setting (0 = no floor)
const dolivMax = ref<number | null>(null)  // долив max-amount (null = not loaded; 0 = no cap)
const pricePercent = ref<number | null>(null)

const bankLabel = computed<string>(
  () => props.requisite?.payment_option_name || props.requisite?.bank_name || '—',
)

// «от X до Y» — the allowed долив size, shown to the trader.
const rangeLabel = computed<string>(() => {
  const cur = props.requisite?.currency ?? ''
  const max = dolivMax.value && dolivMax.value > 0 ? dolivMax.value : null
  if (max == null) return `от ${formatAmount(dolivMin.value || 0)} ${cur}`
  return `от ${formatAmount(dolivMin.value || 0)} до ${formatAmount(max)} ${cur}`
})

// Client-side bounds check (the backend enforces it too; this just guards UX).
const outOfRange = computed<boolean>(() => {
  const a = amount.value
  if (a == null) return false
  if (dolivMin.value > 0 && a < dolivMin.value) return true
  if (dolivMax.value && dolivMax.value > 0 && a > dolivMax.value) return true
  return false
})

// Reset on open + lazily load the долив config (bounds + price percent).
watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return
    amount.value = undefined
    if (dolivMax.value === null) {
      try {
        const cfg = (await dolivService.config()).data
        dolivMin.value = cfg.min_amount ?? 0
        dolivMax.value = cfg.max_amount ?? 0
        pricePercent.value = cfg.price_percent ?? 0
      } catch {
        dolivMin.value = 0
        dolivMax.value = 0
        pricePercent.value = null
      }
    }
  },
)

const canSubmit = computed(
  () => !!props.requisite && !!amount.value && amount.value > 0 && !outOfRange.value,
)

async function submit() {
  if (!props.requisite || !amount.value) return
  busy.value = true
  try {
    const { data } = await dolivService.create({ requisite_id: props.requisite.id, amount: amount.value })
    toast.success('Долив создан')
    emit('created', data)
    emit('update:modelValue', false)
  } catch (e: any) {
    toast.error(e?.response?.data?.detail || e?.response?.data?.message || 'Не удалось создать долив')
  } finally {
    busy.value = false
  }
}
</script>
