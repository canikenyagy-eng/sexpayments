<template>
  <div
    ref="anchorRef"
    :class="rootClass"
    @mouseenter="onEnter"
    @mouseleave="onLeave"
  >
    <div :class="variant === 'ring' ? '' : 'flex items-center gap-3'">
      <div class="relative h-8 w-8 shrink-0">
        <svg class="h-full w-full -rotate-90" viewBox="0 0 36 36">
          <!-- track -->
          <circle cx="18" cy="18" r="15.915" fill="none" class="stroke-bg-hover" stroke-width="4" />
          <!-- completed turnover (green) — drawn first, from 0%.
               Matches the green status badge tint so the "успешные"
               state is visually consistent across UI surfaces. -->
          <circle
            v-if="completedPercent > 0"
            cx="18" cy="18" r="15.915"
            fill="none"
            class="stroke-status-success transition-all duration-300"
            stroke-width="4"
            :stroke-dasharray="`${completedPercent}, 100`"
          />
          <!-- active orders (warning) — drawn on top of completed, offset by completed length -->
          <circle
            v-if="activePercent > 0"
            cx="18" cy="18" r="15.915"
            fill="none"
            class="stroke-status-warning transition-all duration-300"
            stroke-width="4"
            :stroke-dasharray="`${activePercent}, 100`"
            :stroke-dashoffset="-completedPercent"
          />
        </svg>
      </div>
      <div v-if="variant !== 'ring'" class="flex flex-col">
        <div class="flex items-center gap-1.5 text-[11px] font-medium text-text-muted whitespace-nowrap">
          <span class="font-mono text-text-main">{{ formatAmount(current) }}</span>
          <span>/</span>
          <span class="font-mono text-text-main">{{ formatAmount(max) }}</span>
        </div>
        <div class="flex items-center gap-1.5 text-[11px] font-medium text-text-muted whitespace-nowrap">
          <span>Остаток</span>
          <span class="font-mono text-text-main">{{ formatAmount(remaining) }}</span>
        </div>
      </div>
    </div>

    <Teleport v-if="variant !== 'ring'" to="body">
      <div
        v-if="visible"
        class="pointer-events-none fixed z-[100] w-64 rounded-xl border border-border bg-bg-surface p-3 shadow-[0_20px_50px_rgba(0,0,0,0.5)]"
        :style="tooltipStyle"
        role="tooltip"
      >
        <div class="mb-2 text-xs font-bold uppercase tracking-wider text-accent">
          Лимиты реквизита
        </div>
        <dl class="grid grid-cols-[1fr_auto] gap-x-3 gap-y-1 text-xs">
          <dt class="text-text-muted">Мин. чек:</dt>
          <dd class="font-mono text-text-main">{{ formatAmount(minCheck) }}</dd>
          <dt class="text-text-muted">Макс. чек:</dt>
          <dd class="font-mono text-text-main">{{ formatAmount(maxCheck) }}</dd>
          <dt class="flex items-center gap-1.5 text-text-muted">
            <span class="inline-block h-2 w-2 rounded-full bg-status-success" />
            Успешные:
          </dt>
          <dd class="font-mono text-text-main">{{ formatAmount(current) }}</dd>
          <dt class="flex items-center gap-1.5 text-text-muted">
            <span class="inline-block h-2 w-2 rounded-full bg-status-warning" />
            В работе:
          </dt>
          <dd class="font-mono text-text-main">{{ formatAmount(active) }}</dd>
          <dt class="text-text-muted">Макс. лимит:</dt>
          <dd class="font-mono text-text-main">{{ formatAmount(max) }}</dd>
          <dt class="text-text-muted">Остаток:</dt>
          <dd class="font-mono text-text-main">{{ formatAmount(remaining) }}</dd>
          <dt class="text-text-muted">Тип лимита:</dt>
          <dd class="text-text-main">{{ limitKindLabel }}</dd>
        </dl>
      </div>
    </Teleport>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { formatAmount } from '@/utils/format'
import type { RequisiteLimit } from '@/types'

const props = withDefaults(
  defineProps<{
    limits?: RequisiteLimit | null
    /**
     * `full` — ring + current/max/remaining text + hover tooltip (default,
     *           used in the requisites table cell).
     *
     * `ring` — bare SVG ring only, no text, no tooltip, no pointer
     *           interaction. Use inside compact cells where the
     *           surrounding context already says what the ring tracks.
     */
    variant?: 'full' | 'ring'
  }>(),
  { variant: 'full' },
)

const current = computed(() => Number(props.limits?.current_daily_turnover ?? 0))
const active = computed(() => Number(props.limits?.active_amount ?? 0))
const max = computed(() => Number(props.limits?.limit_daily ?? 0))
const minCheck = computed(() => Number(props.limits?.limit_min_transaction ?? 0))
const maxCheck = computed(() => Number(props.limits?.limit_max_transaction ?? 0))
const remaining = computed(() => Math.max(max.value - current.value - active.value, 0))

function pctOf(value: number): number {
  if (!max.value || max.value <= 0) return 0
  return Math.min(Math.max((value / max.value) * 100, 0), 100)
}

const completedPercent = computed(() => pctOf(current.value))
// Cap active so completed + active ≤ 100 (otherwise the arcs overlap visually)
const activePercent = computed(() => {
  const remainingCap = Math.max(0, 100 - completedPercent.value)
  return Math.min(pctOf(active.value), remainingCap)
})

const limitKindLabel = computed(() =>
  props.limits?.reset_enabled ? 'дневной (авто-сброс)' : 'общий (без сброса)',
)

const anchorRef = ref<HTMLElement | null>(null)
const visible = ref(false)
const tooltipTop = ref(0)
const tooltipLeft = ref(0)

const TOOLTIP_WIDTH = 256
const TOOLTIP_OFFSET = 8

// `ring` variant is a passive indicator — surrounding cells own the
// tooltip story. Wider stretchy root layout only makes sense in the
// `full` variant where we render the numeric labels next to the ring.
const rootClass = computed(() =>
  props.variant === 'ring'
    ? 'inline-block'
    : 'relative inline-block w-full min-w-[140px] max-w-[220px]',
)

function onEnter() {
  if (props.variant === 'ring') return
  if (!anchorRef.value) return
  const rect = anchorRef.value.getBoundingClientRect()
  const vw = window.innerWidth

  let left = rect.left + rect.width / 2 - TOOLTIP_WIDTH / 2
  left = Math.max(8, Math.min(left, vw - TOOLTIP_WIDTH - 8))

  tooltipLeft.value = left
  tooltipTop.value = rect.bottom + TOOLTIP_OFFSET
  visible.value = true
}

function onLeave() {
  visible.value = false
}

const tooltipStyle = computed(() => ({
  top: `${tooltipTop.value}px`,
  left: `${tooltipLeft.value}px`,
}))
</script>
