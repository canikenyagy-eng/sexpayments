<template>
  <div class="space-y-3">
    <div class="flex items-center gap-2">
      <ReceiptCheckBadge :check="check" />
      <span v-if="check.status === 'failed' && check.refunded" class="rounded-full bg-bg-hover px-2 py-0.5 text-xs text-text-muted">
        списание возвращено
      </span>
    </div>

    <div v-if="check.status === 'failed'" class="rounded-lg bg-status-warning/10 px-3 py-2 text-sm text-status-warning">
      {{ friendlyError }}
    </div>

    <div v-if="(check.verdict?.length ?? 0) > 0" class="space-y-1">
      <p class="text-xs uppercase tracking-wider text-text-muted">Признаки</p>
      <ul class="space-y-1 text-sm text-text-main">
        <li v-for="(v, i) in check.verdict" :key="i" class="flex items-start gap-2">
          <AlertTriangle class="mt-0.5 h-4 w-4 shrink-0 text-status-danger" />
          <span><span class="font-mono">{{ v.type }}</span><span v-if="v.message"> — {{ v.message }}</span></span>
        </li>
      </ul>
    </div>

    <div v-if="check.parsed_data && Object.keys(check.parsed_data).length > 0" class="space-y-1">
      <p class="text-xs uppercase tracking-wider text-text-muted">Данные из чека</p>
      <div class="grid grid-cols-1 gap-1 rounded-lg bg-bg-card px-3 py-2 text-sm sm:grid-cols-2">
        <div v-for="(value, key) in parsedDataEntries" :key="key" class="flex justify-between gap-2">
          <span class="text-text-muted">{{ labelFor(String(key)) }}</span>
          <span class="ml-2 truncate font-mono text-text-main" :title="String(value ?? '')">{{ value ?? '—' }}</span>
        </div>
      </div>
    </div>

    <div class="flex items-center justify-between text-xs text-text-muted">
      <span>
        {{ check.trigger === 'auto' ? 'Авто-проверка' : 'Ручная проверка' }}<template v-if="check.charged">
          · списано {{ Number(check.price_usdt).toFixed(2) }} USDT</template>
      </span>
      <span>{{ formattedTime }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { AlertTriangle } from 'lucide-vue-next'
import ReceiptCheckBadge from './ReceiptCheckBadge.vue'
import { formatDate } from '@/utils/format'
import type { ReceiptCheck } from '@/types'

const props = defineProps<{ check: ReceiptCheck }>()

const LABELS: Record<string, string> = {
  date: 'Дата',
  sum: 'Сумма',
  currency: 'Валюта',
  comission: 'Комиссия',
  status: 'Статус',
  from_bank: 'Банк отправителя',
  from_user: 'Отправитель',
  from_card: 'Карта отправителя',
  from_requisite: 'Реквизит отправителя',
  to_bank: 'Банк получателя',
  to_user: 'Получатель',
  to_card: 'Карта получателя',
  to_requisite: 'Реквизит получателя',
}

function labelFor(key: string): string {
  return LABELS[key] ?? key
}

// User-facing message for failed checks. Provider/adapter codes are
// mapped to a plain Russian string with no implementation details
// (trader sees the same wording regardless of which provider is active).
const ERROR_MESSAGES: Record<string, string> = {
  unsupported_format: 'К проверке доступны только PDF файлы',
  invalid_file: 'К проверке доступны только PDF файлы',
  file_too_large: 'Файл слишком большой',
  unsupported_media_type: 'К проверке доступны только PDF файлы',
  quota_exhausted: 'Сервис проверки временно недоступен',
  upstream_error: 'Сервис проверки временно недоступен',
  upstream_timeout: 'Сервис проверки временно недоступен',
  internal_error: 'Сервис проверки временно недоступен',
  unauthorized: 'Сервис проверки временно недоступен',
  forbidden: 'Сервис проверки временно недоступен',
  not_found: 'Не удалось выполнить проверку',
  method_not_allowed: 'Не удалось выполнить проверку',
  invalid_request: 'Не удалось выполнить проверку',
  adapter_exception: 'Не удалось выполнить проверку',
}

const friendlyError = computed(() => {
  const code = props.check.error_code ?? ''
  return ERROR_MESSAGES[code] ?? 'Не удалось выполнить проверку'
})

const parsedDataEntries = computed(() => props.check.parsed_data ?? {})

const formattedTime = computed(() => {
  const t = props.check.finished_at ?? props.check.created_at
  return formatDate(t)
})
</script>
