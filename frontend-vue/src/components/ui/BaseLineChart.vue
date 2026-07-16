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
      v-else-if="!series.length || !maxLength"
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
      <!-- horizontal gridlines + y-axis labels -->
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

      <!-- area + line for each series -->
      <template v-for="s in computedSeries" :key="s.name">
        <path
          v-if="s.showArea"
          :d="s.area"
          :fill="s.color"
          fill-opacity="0.12"
        />
        <path :d="s.line" fill="none" :stroke="s.color" stroke-width="2" />
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

      <!-- hover marker -->
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
        <circle
          v-for="s in computedSeries"
          :key="`dot-${s.name}`"
          :cx="hover.x"
          :cy="yFor(s.values[hover.index] ?? 0)"
          r="3"
          :fill="s.color"
        />
      </g>
    </svg>

    <div
      v-if="hover"
      class="pointer-events-none absolute z-10 rounded-lg border border-border bg-bg-surface px-3 py-2 text-xs shadow-lg"
      :style="tooltipStyle"
    >
      <slot name="tooltip" :index="hover.index" :x-label="xLabels[hover.index] ?? ''" :series="hoverSeries">
        <div class="mb-1 text-text-muted">{{ xLabels[hover.index] ?? '' }}</div>
        <div v-for="row in hoverSeries" :key="row.name" class="flex items-center gap-1.5">
          <span class="inline-block h-2 w-2 rounded-full" :style="{ background: row.color }" />
          <span class="text-text-muted">{{ row.name }}:</span>
          <span class="font-mono font-bold text-text-main">{{ formatValue(row.value) }}</span>
        </div>
      </slot>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'

export interface ChartSeries {
  name: string
  color: string
  values: number[]
  showArea?: boolean
}

const props = withDefaults(defineProps<{
  series: ChartSeries[]
  xLabels: string[]
  formatValue?: (n: number) => string
  /** Fixed height in pixels. Ignored when ``aspectRatio`` is provided. */
  height?: number
  /**
   * width-to-height ratio used to derive height from the actual container
   * width. e.g. ``2.8`` means a 1120-px wide container renders at 400-px
   * tall. Use this to keep the chart visually balanced across screen sizes.
   */
  aspectRatio?: number
  /** Lower bound when ``aspectRatio`` is used. */
  minHeight?: number
  /** Upper bound when ``aspectRatio`` is used. */
  maxHeight?: number
  /** Bottom padding for x-axis labels. */
  paddingBottom?: number
  /** Top padding above the highest gridline. */
  paddingTop?: number
  /** Left padding for y-axis labels. */
  paddingX?: number
  /** Right padding so the rightmost point doesn't clip. */
  paddingRight?: number
  /** Number of horizontal gridlines (and y labels). */
  gridLines?: number
  /** Approx number of x-axis labels to show. */
  xTickCount?: number
  loading?: boolean
  emptyText?: string
}>(), {
  formatValue: ((n: number) => Math.round(n).toLocaleString('ru-RU')) as any,
  height: 320,
  minHeight: 240,
  maxHeight: 480,
  paddingBottom: 24,
  paddingTop: 12,
  paddingX: 44,
  paddingRight: 12,
  gridLines: 4,
  xTickCount: 6,
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
  return props.height
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

const maxLength = computed(() =>
  props.series.reduce((m, s) => Math.max(m, s.values.length), 0),
)

const maxValue = computed(() => {
  let m = 0
  for (const s of props.series) {
    for (const v of s.values) if (v > m) m = v
  }
  return m || 1
})

function xFor(idx: number) {
  const usable = chartWidth.value - props.paddingX - props.paddingRight
  if (maxLength.value <= 1) return props.paddingX + usable / 2
  return props.paddingX + (usable * idx) / (maxLength.value - 1)
}

function yFor(value: number) {
  const usable = chartHeight.value - props.paddingTop - props.paddingBottom
  return props.paddingTop + usable - (usable * value) / maxValue.value
}

function buildLine(values: number[]) {
  return values
    .map((v, i) => `${i === 0 ? 'M' : 'L'} ${xFor(i)} ${yFor(v)}`)
    .join(' ')
}

function buildArea(values: number[]) {
  if (!values.length) return ''
  const top = values.map((v, i) => `${i === 0 ? 'M' : 'L'} ${xFor(i)} ${yFor(v)}`).join(' ')
  const last = values.length - 1
  const baseY = chartHeight.value - props.paddingBottom
  return `${top} L ${xFor(last)} ${baseY} L ${xFor(0)} ${baseY} Z`
}

const computedSeries = computed(() =>
  props.series.map(s => ({
    ...s,
    line: buildLine(s.values),
    area: s.showArea ? buildArea(s.values) : '',
  })),
)

const yGrid = computed(() => {
  const ticks = props.gridLines
  return Array.from({ length: ticks + 1 }, (_, i) => {
    const value = (maxValue.value * (ticks - i)) / ticks
    return { y: yFor(value), value }
  })
})

const xTicks = computed(() => {
  if (!maxLength.value) return []
  const desired = Math.min(maxLength.value, props.xTickCount)
  const step = Math.max(1, Math.floor(maxLength.value / desired))
  const out: { x: number; label: string }[] = []
  for (let i = 0; i < maxLength.value; i += step) {
    out.push({ x: xFor(i), label: props.xLabels[i] ?? '' })
  }
  const lastIdx = maxLength.value - 1
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
}
const hover = ref<HoverState | null>(null)

const hoverSeries = computed(() => {
  if (!hover.value) return []
  return props.series.map(s => ({
    name: s.name,
    color: s.color,
    value: s.values[hover.value!.index] ?? 0,
  }))
})

function onMove(e: MouseEvent) {
  if (!maxLength.value || !wrapperRef.value) return
  const rect = wrapperRef.value.getBoundingClientRect()
  const cssX = e.clientX - rect.left
  const cssY = e.clientY - rect.top
  let nearest = 0
  let bestDist = Infinity
  for (let i = 0; i < maxLength.value; i++) {
    const dx = Math.abs(xFor(i) - cssX)
    if (dx < bestDist) {
      bestDist = dx
      nearest = i
    }
  }
  hover.value = { index: nearest, x: xFor(nearest), cssX, cssY }
}

const tooltipStyle = computed(() => {
  if (!hover.value || !wrapperRef.value) return {}
  const rect = wrapperRef.value.getBoundingClientRect()
  const tipW = 220
  const left = Math.min(Math.max(0, hover.value.cssX + 12), Math.max(0, rect.width - tipW))
  const top = Math.max(0, hover.value.cssY - 70)
  return { left: `${left}px`, top: `${top}px` }
})
</script>
