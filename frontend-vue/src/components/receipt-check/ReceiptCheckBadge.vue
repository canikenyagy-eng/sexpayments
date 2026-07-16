<template>
  <span
    class="inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-bold"
    :class="cls"
    :title="title"
  >
    <component :is="icon" class="h-3.5 w-3.5" />
    <span v-if="showLabel">{{ label }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Shield, ShieldAlert, ShieldCheck, ShieldOff } from 'lucide-vue-next'
import type { ReceiptCheck } from '@/types'

const props = withDefaults(
  defineProps<{ check?: ReceiptCheck | null; showLabel?: boolean }>(),
  { showLabel: true },
)

// Visual states:
//   * not checked     — neutral (gray Shield)
//   * pending         — neutral
//   * success+clean   — green ShieldCheck
//   * success+dirty   — red ShieldAlert
//   * failed          — orange ShieldOff
//   * cached          — same color as the original verdict (we use is_clean)
const variant = computed(() => {
  const c = props.check
  if (!c) return 'idle'
  if (c.status === 'pending') return 'pending'
  if (c.status === 'failed') return 'failed'
  if (c.is_clean === true) return 'clean'
  if (c.is_clean === false) return 'dirty'
  return 'idle'
})

const icon = computed(() => {
  switch (variant.value) {
    case 'clean': return ShieldCheck
    case 'dirty': return ShieldAlert
    case 'failed': return ShieldOff
    default: return Shield
  }
})

const cls = computed(() => {
  switch (variant.value) {
    case 'clean': return 'bg-status-success/10 text-status-success'
    case 'dirty': return 'bg-status-danger/10 text-status-danger'
    case 'failed': return 'bg-status-warning/10 text-status-warning'
    case 'pending': return 'bg-status-warning/10 text-status-warning animate-pulse'
    default: return 'bg-bg-hover text-text-muted'
  }
})

const label = computed(() => {
  switch (variant.value) {
    case 'clean': return 'Чек чист'
    case 'dirty': return 'Подозрение'
    case 'failed': return 'Ошибка'
    case 'pending': return 'Проверка…'
    default: return 'Не проверен'
  }
})

const title = computed(() => {
  if (!props.check) return 'Чек не проверялся'
  if (props.check.status === 'failed') return `Ошибка проверки: ${props.check.error_message ?? props.check.error_code ?? '—'}`
  if (props.check.status === 'pending') return 'Проверка выполняется…'
  if (props.check.is_clean === false) {
    const flags = (props.check.verdict ?? []).map((v) => v.type).join(', ')
    return `Обнаружены признаки: ${flags || 'неизвестно'}`
  }
  return label.value
})
</script>
