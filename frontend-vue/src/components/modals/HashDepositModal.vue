<template>
  <BaseModal :model-value="open" title="Пополнение хэшем" size="md" @update:model-value="close">
    <!-- Step 1: pick trader + enter TRC20 tx hash -->
    <form v-if="step === 'form'" class="space-y-4" @submit.prevent="verify">
      <UserAutocomplete
        ref="autocompleteRef"
        v-model="userId"
        role="trader"
        label="Трейдер (ID или логин)"
        placeholder="ID или логин"
        hint="Начните вводить ID или логин для поиска"
      />

      <BaseInput
        v-model="txHash"
        label="Хэш транзакции (TRC20)"
        placeholder="Хэш USDT-перевода в сети TRON"
        required
      />

      <div class="flex justify-end gap-2 pt-1">
        <BaseButton type="button" variant="dark" @click="close">Закрыть</BaseButton>
        <BaseButton type="submit" variant="gold" :loading="loading">Пополнить</BaseButton>
      </div>
    </form>

    <!-- Step 2: preview the parsed transfer, confirm the platform wallet by eye -->
    <div v-else class="space-y-4">
      <div class="space-y-3">
        <div class="space-y-1.5">
          <label class="block text-sm font-semibold text-text-secondary">Трейдер</label>
          <div class="input-field font-bold">
            {{ preview?.trader_login || `#${preview?.user_id}` }}
          </div>
        </div>
        <div class="space-y-1.5">
          <label class="block text-sm font-semibold text-text-secondary">Сумма</label>
          <div class="input-field font-bold">
            {{ formatAmount(preview?.amount ?? 0) }} {{ preview?.currency }}
          </div>
        </div>
        <div class="space-y-1.5">
          <label class="block text-sm font-semibold text-text-secondary">Хэш</label>
          <div class="input-field break-all font-mono text-xs">
            {{ preview?.tx_hash }}
          </div>
        </div>
        <div class="space-y-1.5">
          <label class="block text-sm font-semibold text-text-secondary">Кошелёк площадки</label>
          <div class="input-field break-all font-mono text-xs">
            {{ walletHead }}<span class="font-bold text-accent">{{ walletTail }}</span>
          </div>
        </div>
      </div>

      <div class="flex justify-end gap-2 pt-1">
        <BaseButton type="button" variant="dark" @click="backToForm">Назад</BaseButton>
        <BaseButton type="button" variant="success" :loading="confirming" @click="confirm">Подтвердить</BaseButton>
      </div>
    </div>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import UserAutocomplete from '@/components/ui/UserAutocomplete.vue'
import { financesService } from '@/api/services/finances.service'
import { useToast } from '@/composables/useToast'
import { formatAmount } from '@/utils/format'
import type { HashDepositPreview } from '@/types'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  'update:open': [value: boolean]
  'success': []
}>()

const toast = useToast()
const autocompleteRef = ref<InstanceType<typeof UserAutocomplete> | null>(null)

const step = ref<'form' | 'preview'>('form')
const userId = ref<number | null>(null)
const txHash = ref('')
const preview = ref<HashDepositPreview | null>(null)
const loading = ref(false)
const confirming = ref(false)

const walletHead = computed(() => {
  const w = preview.value?.to_address ?? ''
  return w.length > 5 ? w.slice(0, -5) : ''
})
const walletTail = computed(() => (preview.value?.to_address ?? '').slice(-5))

function reset() {
  step.value = 'form'
  userId.value = null
  txHash.value = ''
  preview.value = null
  loading.value = false
  confirming.value = false
  autocompleteRef.value?.reset()
}

function close() {
  emit('update:open', false)
}

function backToForm() {
  step.value = 'form'
  preview.value = null
}

function extractError(e: any, fallback: string): string {
  const d = e?.response?.data
  const msg = d?.error?.message ?? d?.detail ?? d?.message
  return typeof msg === 'string' && msg.trim() ? msg : fallback
}

async function verify() {
  if (!userId.value) {
    toast.error('Выберите трейдера')
    return
  }
  if (!txHash.value.trim()) {
    toast.error('Укажите хэш транзакции')
    return
  }
  loading.value = true
  try {
    const { data } = await financesService.verifyHashDeposit({
      user_id: userId.value,
      tx_hash: txHash.value.trim(),
    })
    preview.value = data
    step.value = 'preview'
  } catch (e: any) {
    toast.error(extractError(e, 'Не удалось проверить транзакцию'))
  } finally {
    loading.value = false
  }
}

async function confirm() {
  if (!preview.value) return
  confirming.value = true
  try {
    const { data } = await financesService.confirmHashDeposit({
      user_id: preview.value.user_id,
      tx_hash: preview.value.tx_hash,
    })
    toast.success(`Пополнено: ${formatAmount(data.amount)} ${data.currency}`)
    emit('success')
    close()
  } catch (e: any) {
    toast.error(extractError(e, 'Не удалось выполнить пополнение'))
  } finally {
    confirming.value = false
  }
}

watch(() => props.open, (val) => {
  if (val) reset()
})
</script>
