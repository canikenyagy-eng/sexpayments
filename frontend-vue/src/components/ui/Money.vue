<template>
  <span :class="['inline-flex items-baseline gap-1 leading-none', sizeClass, variantClass]" :title="title">
    <span v-if="mode === 'symbol'" class="text-text-muted">{{ symbol }}</span>
    <span class="font-semibold tabular-nums text-text-main">{{ formatted }}</span>
    <span v-if="mode === 'code'" class="text-text-muted">{{ codeLabel }}</span>
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatAmount, formatInt } from '@/utils/format'

const props = withDefaults(
  defineProps<{
    /** Numeric amount (also accepts string forms coming from the API). */
    amount: number | string | null | undefined
    /** Currency code (RUB / USDT / USD / AZN / EUR / …). */
    currency: string
    /**
     * `symbol` — show currency glyph on the LEFT (`$ 50.50`, `₽ 1 000`).
     *
     * `code` — show currency code on the RIGHT (`50.50 USDT`).
     *
     * `none` — render just the formatted number, no symbol or code.
     * Useful when the surrounding label already conveys the unit (e.g.
     * "курс 75,54" — context says it's a rate, not a currency).
     *
     * Default `code` because the existing admin tables show "1 000 RUB" /
     * "50 USDT" style.
     */
    mode?: 'symbol' | 'code' | 'none'
    size?: 'xs' | 'sm' | 'md'
    /**
     * `badge` — subtle pill background with horizontal padding (default —
     * matches the user's "типа бейджик" spec, used in tables/grids).
     *
     * `plain` — no background, no padding, just inline text. Use inside
     * tight cells where the surrounding container already provides
     * spacing.
     */
    variant?: 'badge' | 'plain'
  }>(),
  {
    mode: 'code',
    size: 'sm',
    variant: 'badge',
  },
)

// USDT renders as `$` per product copy — traders see all USDT-denominated
// balances/profits with a dollar glyph rather than an unfamiliar token
// symbol.
const CURRENCY_SYMBOLS: Record<string, string> = {
  RUB: '₽',
  USDT: '$',
  USD: '$',
  AZN: '₼',
  EUR: '€',
}

const numericAmount = computed<number | null>(() => {
  if (props.amount === null || props.amount === undefined || props.amount === '') return null
  const n = typeof props.amount === 'string' ? Number(props.amount) : props.amount
  return Number.isFinite(n) ? n : null
})

const codeLabel = computed(() => (props.currency || '').toUpperCase())

const symbol = computed(() => CURRENCY_SYMBOLS[codeLabel.value] ?? codeLabel.value)

const formatted = computed(() => {
  const n = numericAmount.value
  if (n === null) return '—'
  // Match the existing admin convention: integer RUB amounts render without
  // trailing ",00" because the table is already busy.
  if (codeLabel.value === 'RUB' && n % 1 === 0) return formatInt(n)
  return formatAmount(n)
})

const sizeClass = computed(() => {
  switch (props.size) {
    case 'xs': return 'text-xs'
    case 'md': return 'text-base'
    default: return 'text-sm'
  }
})

const variantClass = computed(() =>
  // `badge` is a pill: subtle background + horizontal padding so it reads
  // as a chip in dense tables. `plain` drops both so the number flows
  // inline like any other text.
  props.variant === 'badge' ? 'bg-bg-card rounded-md px-1.5 py-0.5' : '',
)

// Always full-form on hover so the abbreviated `1 000 ₽` still reveals the
// machine-readable code on inspection.
const title = computed(() => `${formatted.value} ${codeLabel.value}`)
</script>
