<template>
  <BaseBadge :color="color">
    <span class="inline-flex items-center gap-1.5">
      <Loader2 v-if="showCountdown" class="h-3 w-3 shrink-0 animate-spin" />
      <component v-else-if="icon" :is="icon" class="h-3 w-3 shrink-0" />
      <span>{{ label }}</span>
      <span
        v-if="showCountdown"
        class="font-mono tabular-nums"
        :class="isExpired ? 'text-status-danger' : ''"
      >
        {{ countdown }}
      </span>
    </span>
  </BaseBadge>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import {
  AlertTriangle,
  Archive,
  Ban,
  Check,
  CheckCircle2,
  CircleDashed,
  CircleDot,
  Clock,
  Eye,
  FileText,
  Lock,
  Loader2,
  MinusCircle,
  Power,
  Undo2,
  Zap,
  X,
  XCircle,
} from 'lucide-vue-next'
import BaseBadge from './BaseBadge.vue'

type StatusContext = 'order' | 'merchant' | 'dispute' | 'withdrawal' | 'requisite' | 'user' | 'doliv'

const props = withDefaults(defineProps<{
  status: string
  /**
   * Optional ISO date / timestamp — when provided together with
   * status === 'pending', the badge renders a spinner on the left
   * and an mm:ss countdown to this deadline on the right.
   */
  expiresAt?: string | number | Date | null
  /**
   * Domain hint. Some statuses (e.g. `pending`) mean different things in
   * different domains — orders await payment, merchants await moderation.
   * Pass the entity context to get the correct label / icon.
   */
  context?: StatusContext
}>(), {
  context: 'order',
})

type Color = 'success' | 'danger' | 'warning' | 'info' | 'default' | 'gold'

type Cfg = { label: string; color: Color; icon: any }

const statusConfig: Record<string, Cfg> = {
  // Orders
  created: { label: 'Создана', color: 'default', icon: CircleDashed },
  pending: { label: 'Ожидание', color: 'warning', icon: Clock },
  receipt_uploaded: { label: 'Чек загружен', color: 'info', icon: FileText },
  success: { label: 'Успешно', color: 'success', icon: CheckCircle2 },
  disputed: { label: 'Спор', color: 'danger', icon: AlertTriangle },
  canceled: { label: 'Отменена', color: 'danger', icon: XCircle },
  failed: { label: 'Неудача', color: 'danger', icon: XCircle },
  refunded: { label: 'Возврат', color: 'warning', icon: Undo2 },
  // Payouts (created / canceled shared with orders above)
  claimed: { label: 'Взято', color: 'info', icon: CircleDot },
  awaiting_check: { label: 'На проверке', color: 'warning', icon: Eye },
  completed: { label: 'Выполнено', color: 'success', icon: CheckCircle2 },
  expired: { label: 'Истекло', color: 'default', icon: MinusCircle },
  // Users / Traders
  active: { label: 'Активен', color: 'success', icon: Power },
  blocked: { label: 'Заблокирован', color: 'danger', icon: Lock },
  enabled: { label: 'Включён', color: 'success', icon: CheckCircle2 },
  disabled: { label: 'Выключен', color: 'default', icon: MinusCircle },
  inactive: { label: 'Не активен', color: 'default', icon: MinusCircle },
  archived: { label: 'Архив', color: 'default', icon: Archive },
  // Merchants
  test: { label: 'Тест', color: 'info', icon: Zap },
  // Withdrawals
  rejected: { label: 'Отклонён', color: 'danger', icon: Ban },
  // Disputes
  open: { label: 'Открыт', color: 'warning', icon: CircleDot },
  resolved: { label: 'Решён', color: 'success', icon: CheckCircle2 },
  // Boolean
  true: { label: 'Да', color: 'success', icon: Check },
  false: { label: 'Нет', color: 'default', icon: X },
}

// Per-context overrides — only entries where the meaning of the same
// status string differs between domains. Falls through to `statusConfig`.
const contextOverrides: Partial<Record<StatusContext, Record<string, Cfg>>> = {
  merchant: {
    pending: { label: 'На модерации', color: 'warning', icon: Eye },
  },
  doliv: {
    // A claimed долив is «taken in work» by the доливщик.
    claimed: { label: 'В работе', color: 'info', icon: CircleDot },
  },
}

const cfg = computed<Cfg>(() => {
  const override = contextOverrides[props.context]?.[props.status]
  if (override) return override
  return (
    statusConfig[props.status] ?? {
      label: props.status,
      color: 'default' as const,
      icon: null,
    }
  )
})
const label = computed(() => cfg.value.label)
const color = computed(() => cfg.value.color)
const icon = computed(() => cfg.value.icon)

const expiresTs = computed<number | null>(() => {
  if (!props.expiresAt) return null
  const raw = props.expiresAt
  // Backend отдаёт UTC без таймзоны ("YYYY-MM-DDTHH:mm:ss[.ffffff]"),
  // а JS без `Z` парсит такую строку как local time. Дописываем `Z`,
  // чтобы дата корректно считалась UTC.
  let input: string | number | Date = raw as any
  if (typeof raw === 'string' && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$/.test(raw)) {
    input = `${raw}Z`
  }
  const d = new Date(input as string | number | Date)
  const t = d.getTime()
  return Number.isFinite(t) ? t : null
})

const now = ref(Date.now())
let timer: number | null = null

function tick() {
  now.value = Date.now()
}

onMounted(() => {
  if (expiresTs.value !== null) {
    timer = window.setInterval(tick, 1000)
  }
})

onBeforeUnmount(() => {
  if (timer !== null) {
    window.clearInterval(timer)
    timer = null
  }
})

const remainingSec = computed(() => {
  if (expiresTs.value === null) return 0
  return Math.max(0, Math.floor((expiresTs.value - now.value) / 1000))
})

const showCountdown = computed(
  () => props.status === 'pending' && expiresTs.value !== null,
)

const isExpired = computed(() => showCountdown.value && remainingSec.value <= 0)

const countdown = computed(() => {
  const s = remainingSec.value
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
})
</script>
