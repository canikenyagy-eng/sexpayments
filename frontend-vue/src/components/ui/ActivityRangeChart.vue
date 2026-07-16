<template>
  <div ref="wrapperRef" class="relative w-full">
    <div
      v-if="loading"
      :style="{ height: `${chartHeight}px` }"
      class="flex items-center justify-center text-sm text-text-muted"
    >
      Загрузка…
    </div>
    <div
      v-else-if="!hasData"
      :style="{ height: `${chartHeight}px` }"
      class="flex items-center justify-center text-sm text-text-muted"
    >
      {{ emptyText }}
    </div>
    <svg
      v-else
      :width="chartWidth"
      :height="chartHeight"
      :viewBox="`0 0 ${chartWidth} ${chartHeight}`"
      class="block"
      @mousemove="onMove"
      @mouseleave="hover = null"
    >
      <!-- horizontal gridlines + y-axis labels (amount) -->
      <g v-for="(g, i) in yGrid" :key="`g-${i}`">
        <line
          :x1="paddingX"
          :x2="chartWidth - paddingRight"
          :y1="g.y"
          :y2="g.y"
          stroke="currentColor"
          stroke-opacity="0.08"
        />
        <text
          :x="paddingX - 6"
          :y="g.y + 3"
          text-anchor="end"
          font-size="9"
          fill="currentColor"
          fill-opacity="0.5"
        >
          {{ formatValue(g.value) }}
        </text>
      </g>

      <!-- floating bars: one per merged range, filled by the bucket's trader count -->
      <template v-for="(b, i) in buckets" :key="`b-${i}`">
        <rect
          v-for="(bar, j) in b.bars"
          :key="`b-${i}-${j}`"
          :x="xFor(i) - barWidth / 2"
          :y="yFor(bar.max)"
          :width="barWidth"
          :height="Math.max(1, yFor(bar.min) - yFor(bar.max))"
          rx="2"
          :fill="colorForTraders(b.trader_count)"
        />
      </template>

      <!-- x-axis labels (sparse) -->
      <text
        v-for="(t, i) in xTicks"
        :key="`x-${i}`"
        :x="t.x"
        :y="chartHeight - paddingBottom + 14"
        text-anchor="middle"
        font-size="9"
        fill="currentColor"
        fill-opacity="0.5"
      >
        {{ t.label }}
      </text>

      <!-- hover crosshair -->
      <g v-if="hover">
        <line
          :x1="hover.x"
          :x2="hover.x"
          :y1="paddingTop"
          :y2="chartHeight - paddingBottom"
          stroke="currentColor"
          stroke-opacity="0.25"
          stroke-dasharray="3 3"
        />
        <line
          :x1="paddingX"
          :x2="chartWidth - paddingRight"
          :y1="hover.cssY"
          :y2="hover.cssY"
          stroke="currentColor"
          stroke-opacity="0.15"
          stroke-dasharray="3 3"
        />
      </g>
    </svg>

    <div
      v-if="hover"
      class="pointer-events-none absolute z-10 rounded-lg border border-border bg-bg-surface px-3 py-2 text-xs shadow-lg"
      :style="tooltipStyle"
    >
      <div class="mb-1 text-text-muted">{{ xLabels[hover.index] ?? '' }}</div>
      <div class="flex items-center gap-1.5">
        <span
          class="inline-block h-2 w-2 rounded-full"
          :style="{ background: colorForTraders(hover.traderCount) }"
        />
        <span class="text-text-muted">Трейдеров:</span>
        <span class="font-mono font-bold text-text-main">{{ hover.traderCount }}</span>
      </div>
      <div v-if="hover.inBar" class="mt-0.5 text-text-muted">
        Уровень <span class="font-mono text-text-main">{{ formatValue(hover.amount) }}</span>:
        <span class="font-mono font-bold text-text-main">{{ hover.requisites }}</span> реквиз.
      </div>
      <div v-else class="mt-0.5 text-text-muted">Нет реквизитов на этом уровне</div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { ActivityBucket } from '@/api/services/stats.service'
import { colorForTraders } from '@/utils/activityColor'

const props = withDefaults(defineProps<{
  buckets: ActivityBucket[]
  xLabels: string[]
  formatValue?: (n: number) => string
  aspectRatio?: number
  minHeight?: number
  maxHeight?: number
  paddingBottom?: number
  paddingTop?: number
  paddingX?: number
  paddingRight?: number
  gridLines?: number
  xTickCount?: number
  /** Max bar width in px so sparse charts don't render giant blocks. */
  maxBarWidth?: number
  loading?: boolean
  emptyText?: string
}>(), {
  formatValue: ((n: number) => Math.round(n).toLocaleString('ru-RU')) as any,
  aspectRatio: 2.6,
  minHeight: 260,
  maxHeight: 520,
  paddingBottom: 24,
  paddingTop: 12,
  paddingX: 52,
  paddingRight: 12,
  gridLines: 4,
  xTickCount: 8,
  maxBarWidth: 34,
  loading: false,
  emptyText: 'Нет данных',
})

const wrapperRef = ref<HTMLElement | null>(null)
const chartWidth = ref(800)
const chartHeight = computed(() => {
  if (props.aspectRatio && props.aspectRatio > 0) {
    const derived = chartWidth.value / props.aspectRatio
    return Math.min(props.maxHeight, Math.max(props.minHeight, derived))
  }
  return props.minHeight
})

let resizeObserver: ResizeObserver | null = null

function syncWidth() {
  const el = wrapperRef.value
  if (!el) return
  const w = el.clientWidth
  if (w > 0) chartWidth.value = w
}

onMounted(() => {
  syncWidth()
  if (typeof ResizeObserver !== 'undefined' && wrapperRef.value) {
    resizeObserver = new ResizeObserver(() => syncWidth())
    resizeObserver.observe(wrapperRef.value)
  } else {
    window.addEventListener('resize', syncWidth)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  window.removeEventListener('resize', syncWidth)
})

const hasData = computed(() => props.buckets.some(b => b.bars.length > 0))

const maxValue = computed(() => {
  let m = 0
  for (const b of props.buckets) {
    for (const bar of b.bars) if (bar.max > m) m = bar.max
  }
  return m || 1
})

const count = computed(() => props.buckets.length)

const barWidth = computed(() => {
  const usable = chartWidth.value - props.paddingX - props.paddingRight
  const slot = count.value > 0 ? usable / count.value : usable
  return Math.max(2, Math.min(props.maxBarWidth, slot * 0.7))
})

function xFor(idx: number) {
  const usable = chartWidth.value - props.paddingX - props.paddingRight
  if (count.value <= 1) return props.paddingX + usable / 2
  const slot = usable / count.value
  return props.paddingX + slot * (idx + 0.5)
}

function yFor(value: number) {
  const usable = chartHeight.value - props.paddingTop - props.paddingBottom
  return props.paddingTop + usable - (usable * value) / maxValue.value
}

function valueForY(y: number) {
  const usable = chartHeight.value - props.paddingTop - props.paddingBottom
  return ((props.paddingTop + usable - y) * maxValue.value) / usable
}

const yGrid = computed(() => {
  const ticks = props.gridLines
  return Array.from({ length: ticks + 1 }, (_, i) => {
    const value = (maxValue.value * (ticks - i)) / ticks
    return { y: yFor(value), value }
  })
})

const xTicks = computed(() => {
  if (!count.value) return []
  const desired = Math.min(count.value, props.xTickCount)
  const step = Math.max(1, Math.floor(count.value / desired))
  const out: { x: number; label: string }[] = []
  for (let i = 0; i < count.value; i += step) {
    out.push({ x: xFor(i), label: props.xLabels[i] ?? '' })
  }
  const lastIdx = count.value - 1
  if (out.length === 0 || out[out.length - 1].x !== xFor(lastIdx)) {
    out.push({ x: xFor(lastIdx), label: props.xLabels[lastIdx] ?? '' })
  }
  return out
})

interface HoverState {
  index: number
  x: number
  cssX: number
  cssY: number
  amount: number
  traderCount: number
  inBar: boolean
  requisites: number
}
const hover = ref<HoverState | null>(null)

function onMove(e: MouseEvent) {
  if (!count.value || !wrapperRef.value) return
  const rect = wrapperRef.value.getBoundingClientRect()
  const cssX = e.clientX - rect.left
  const cssY = e.clientY - rect.top
  let nearest = 0
  let bestDist = Infinity
  for (let i = 0; i < count.value; i++) {
    const dx = Math.abs(xFor(i) - cssX)
    if (dx < bestDist) {
      bestDist = dx
      nearest = i
    }
  }
  const bucket = props.buckets[nearest]
  const amount = valueForY(cssY)
  let inBar = false
  let requisites = 0
  for (const bar of bucket?.bars ?? []) {
    if (amount >= bar.min && amount <= bar.max) {
      inBar = true
      const seg = bar.segments.find(s => amount >= s.from_amount && amount <= s.to_amount)
      requisites = seg?.requisites ?? 0
      break
    }
  }
  hover.value = {
    index: nearest,
    x: xFor(nearest),
    cssX,
    cssY,
    amount,
    traderCount: bucket?.trader_count ?? 0,
    inBar,
    requisites,
  }
}

const tooltipStyle = computed(() => {
  if (!hover.value || !wrapperRef.value) return {}
  const rect = wrapperRef.value.getBoundingClientRect()
  const tipW = 200
  const left = Math.min(Math.max(0, hover.value.cssX + 12), Math.max(0, rect.width - tipW))
  const top = Math.max(0, hover.value.cssY - 60)
  return { left: `${left}px`, top: `${top}px` }
})
</script>
