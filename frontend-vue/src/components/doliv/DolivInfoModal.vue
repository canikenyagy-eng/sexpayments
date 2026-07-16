<template>
  <BaseModal :model-value="modelValue" title="Долив" size="lg" @update:model-value="$emit('update:modelValue', $event)">
    <div v-if="doliv" class="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
      <div class="sm:col-span-2">
        <span class="text-text-muted">UUID:</span>
        <UuidDisplay :value="doliv.id" :truncate="false" show-icon class="ml-1" />
      </div>
      <div>
        <span class="text-text-muted">Сумма:</span>
        <span class="ml-2 font-bold text-text-main">{{ formatAmount(Number(doliv.amount)) }} {{ doliv.currency }}</span>
      </div>
      <div>
        <span class="text-text-muted">Сумма USDT:</span>
        <span class="ml-2 text-text-main">{{ doliv.amount_usdt != null ? formatAmount(Number(doliv.amount_usdt)) : '—' }}</span>
      </div>
      <div>
        <span class="text-text-muted">Курс:</span>
        <span class="ml-2 text-text-main">{{ doliv.exchange_rate != null ? formatAmount(Number(doliv.exchange_rate)) : '—' }}</span>
      </div>
      <div>
        <span class="text-text-muted">Цена:</span>
        <span class="ml-2 text-text-main">{{ doliv.price_usdt != null ? formatAmount(Number(doliv.price_usdt)) + ' USDT' : '—' }}</span>
      </div>
      <div v-if="doliv.executor_reward_usdt != null">
        <span class="text-text-muted">Награда доливщику:</span>
        <span class="ml-2 text-status-success">{{ formatAmount(Number(doliv.executor_reward_usdt)) }} USDT</span>
      </div>
      <div>
        <span class="text-text-muted">Статус:</span>
        <span class="ml-2"><StatusBadge :status="doliv.status" :expires-at="doliv.claim_expires_at" context="doliv" /></span>
      </div>
      <div>
        <span class="text-text-muted">Метод:</span>
        <span class="ml-2"><MethodBadge :method="doliv.payment_method" /></span>
      </div>
      <div class="sm:col-span-2">
        <span class="text-text-muted">Реквизит:</span>
        <span class="ml-2 font-mono text-text-main">{{ doliv.req_number || '—' }}</span>
        <span v-if="doliv.req_extra" class="ml-1 text-text-muted">· {{ doliv.req_extra }}</span>
      </div>
      <div v-if="doliv.req_holder" class="sm:col-span-2">
        <span class="text-text-muted">Держатель:</span>
        <span class="ml-2 text-text-main">{{ doliv.req_holder }}</span>
      </div>
      <div>
        <span class="text-text-muted">Создан:</span>
        <span class="ml-2 text-text-main">{{ formatDate(doliv.created_at) }}</span>
      </div>
      <div v-if="doliv.claimed_at">
        <span class="text-text-muted">Взято в работу:</span>
        <span class="ml-2 text-text-main">{{ formatDate(doliv.claimed_at) }}</span>
      </div>
      <div v-if="doliv.expires_at">
        <span class="text-text-muted">Истекает:</span>
        <span class="ml-2 text-text-main">{{ formatDate(doliv.expires_at) }}</span>
      </div>
      <div v-if="doliv.completed_at">
        <span class="text-text-muted">Завершён:</span>
        <span class="ml-2 text-text-main">{{ formatDate(doliv.completed_at) }}</span>
      </div>
      <div v-if="doliv.canceled_at">
        <span class="text-text-muted">Отменён:</span>
        <span class="ml-2 text-text-main">{{ formatDate(doliv.canceled_at) }}</span>
      </div>
      <div v-if="doliv.has_receipt" class="sm:col-span-2">
        <span class="text-text-muted">Чек:</span>
        <button class="ml-2 font-semibold text-accent transition hover:underline" @click="downloadCheck">
          Скачать
        </button>
      </div>
    </div>
    <template #footer>
      <BaseButton variant="dark" @click="$emit('update:modelValue', false)">Закрыть</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import { dolivService } from '@/api/services/doliv.service'
import { useToast } from '@/composables/useToast'
import { downloadReceipt, preOpenReceiptTab } from '@/utils/receipt'
import { formatAmount, formatDate } from '@/utils/format'
import type { Doliv } from '@/types'

const props = defineProps<{ modelValue: boolean; doliv: Doliv | null }>()
defineEmits<{ 'update:modelValue': [boolean] }>()

const toast = useToast()

async function downloadCheck() {
  if (!props.doliv) return
  const tab = preOpenReceiptTab()
  try {
    await downloadReceipt(dolivService.receiptUrl(props.doliv.id), tab)
  } catch (e: any) {
    toast.error(e?.message || 'Не удалось скачать чек')
  }
}
</script>
