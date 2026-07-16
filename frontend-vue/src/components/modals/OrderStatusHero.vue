<template>
  <!-- Status + amount hero. Border + background follow the status colour so the
       card itself reads as "this order is X". Shared by the admin & trader order
       modals. -->
  <section
    class="space-y-2 rounded-2xl border p-4 shadow-prime transition-colors"
    :class="statusCardClass"
  >
    <div class="flex items-center justify-between">
      <StatusBadge :status="status" :expires-at="expiresAt" />
      <MethodBadge :method="paymentMethod" />
    </div>
    <div class="flex items-baseline gap-1.5 text-3xl font-black tabular-nums text-text-main">
      <span>{{ formatAmount(Number(amount)) }}</span>
      <span class="text-2xl text-text-muted">{{ currencySymbol }}</span>
    </div>
    <div class="text-xs text-text-muted">
      <Money
        v-if="amountUsdt != null"
        :amount="amountUsdt"
        currency="USDT"
        mode="code"
        variant="plain"
      />
      <span v-else>—</span>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import Money from '@/components/ui/Money.vue'
import { formatAmount } from '@/utils/format'
import type { OrderStatus, PaymentMethod } from '@/types'

const props = defineProps<{
  status: OrderStatus
  paymentMethod: PaymentMethod
  amount: number | string
  currency: string
  amountUsdt?: number | string | null
  // Optional deadline so the status badge can show the live countdown.
  expiresAt?: string | null
}>()

const CURRENCY_SYMBOLS: Record<string, string> = {
  RUB: '₽',
  USDT: '$',
  USD: '$',
  EUR: '€',
  AZN: '₼',
}
const currencySymbol = computed(() =>
  props.currency ? CURRENCY_SYMBOLS[props.currency] ?? props.currency : '',
)

// Tints the hero card to match the status badge colour; neutral for "default".
type StatusColor = 'success' | 'danger' | 'warning' | 'info' | 'default'
const STATUS_COLOR: Record<string, StatusColor> = {
  created: 'default',
  pending: 'warning',
  receipt_uploaded: 'info',
  success: 'success',
  disputed: 'danger',
  canceled: 'danger',
  failed: 'danger',
  refunded: 'warning',
}
const STATUS_CARD_CLASS: Record<StatusColor, string> = {
  success: 'border-status-success/40 bg-status-success/10',
  danger: 'border-status-danger/40 bg-status-danger/10',
  warning: 'border-status-warning/40 bg-status-warning/10',
  info: 'border-status-info/40 bg-status-info/10',
  default: 'border-border bg-bg-card',
}
const statusCardClass = computed(() => STATUS_CARD_CLASS[STATUS_COLOR[props.status] || 'default'])
</script>
