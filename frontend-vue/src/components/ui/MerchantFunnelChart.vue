<template>
  <div ref="wrapperRef" class="relative w-full">
    <div v-if="loading" class="flex h-[240px] items-center justify-center text-sm text-text-muted">
      Загрузка…
    </div>
    <div v-else-if="!points.length" class="flex h-[240px] items-center justify-center text-sm text-text-muted">
      {{ emptyText }}
    </div>
    <template v-else>
      <!-- Legend -->
      <div class="mb-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        <span v-for="s in series" :key="s.key" class="flex items-center gap-1.5">
          <span class="h-1 w-4 rounded-sm" :style="{ background: s.color }" />
          <span class="text-text-muted">{{ s.label }}</span>
        </span>
      </div>

      <!-- Rendered 1:1 (width attr == container width) so viewBox units == CSS
           pixels — no letterboxing, hover/tooltip stay pixel-accurate. -->
      <svg
        :width="W"
        :height="H"
        :viewBox="`0 0 ${W} ${H}`"
        class="block"
        @mousemove="onMove"
        @mouseleave="hoverIdx = null"
      >
        <!-- Y grid + labels -->
        <g>
          <line
            v-for="(gy, i) in yTicks"
            :key="'g' + i"
            :x1="PAD_L" :x2="W - PAD_R" :y1="gy.y" :y2="gy.y"
            stroke="currentColor" stroke-opacity="0.08"
          />
          <text
            v-for="(gy, i) in yTicks"
            :key="'t' + i"
            :x="PAD_L - 6" :y="gy.y + 3"
            text-anchor="end" font-size="10" fill="currentColor" fill-opacity="0.45"
          >{{ formatInt(gy.value) }}</text>
        </g>

        <!-- Series lines -->
        <polyline
          v-for="s in series"
          :key="s.key"
          :points="linePoints(s.key)"
          fill="none" :stroke="s.color" stroke-width="1.5"
          stroke-linejoin="round" stroke-linecap="round"
        />

        <!-- Hover crosshair + dots -->
        <g v-if="hoverIdx !== null">
          <line
            :x1="xAt(hoverIdx)" :x2="xAt(hoverIdx)" :y1="PAD_T" :y2="H - PAD_B"
            stroke="currentColor" stroke-opacity="0.25"
          />
          <circle
            v-for="s in series" :key="'d' + s.key"
            :cx="xAt(hoverIdx)" :cy="yAt(points[hoverIdx][s.key])" r="2.5" :fill="s.color"
          />
        </g>

        <!-- X ticks (sparse) -->
        <text
          v-for="t in xTicks"
          :key="'x' + t.i"
          :x="xAt(t.i)" :y="H - 6"
          text-anchor="middle" font-size="10" fill="currentColor" fill-opacity="0.45"
        >{{ t.label }}</text>
      </svg>

      <!-- Tooltip (positioned in CSS px == viewBox units thanks to 1:1 render) -->
      <div
        v-if="hoverIdx !== null"
        class="pointer-events-none absolute z-10 rounded-lg border border-border bg-bg-card px-3 py-2 text-xs shadow-lg"
        :style="tooltipStyle"
      >
        <div class="mb-1 font-bold text-text-main">{{ xLabels[hoverIdx] }}</div>
        <div v-for="s in series" :key="'tt' + s.key" class="flex items-center justify-between gap-4">
          <span class="flex items-center gap-1.5 text-text-muted">
            <span class="h-1.5 w-1.5 rounded-full" :style="{ background: s.color }" />{{ s.label }}
          </span>
          <span class="font-bold text-text-main">{{ formatInt(points[hoverIdx][s.key]) }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { formatInt } from '@/utils/format'
import type { MerchantTimeseriesPoint } from '@/api/services/stats.service'

const props = withDefaults(
  defineProps<{
    points: MerchantTimeseriesPoint[]
    xLabels: string[]
    loading?: boolean
    emptyText?: string
  }>(),
  { loading: false, emptyText: 'Нет данных за выбранный период' },
)

const H = 240
const PAD_L = 44
const PAD_R = 12
const PAD_T = 12
const PAD_B = 22

// Width tracks the real container (rendered 1:1) — mirrors ActivityRangeChart.
const wrapperRef = ref<HTMLElement | null>(null)
const chartWidth = ref(800)
const W = computed(() => chartWidth.value)

let resizeObserver: ResizeObserver | null = null
function syncWidth() {
  const el = wrapperRef.value
  if (el && el.clientWidth > 0) chartWidth.value = el.clientWidth
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

type SeriesKey = 'requests' | 'created' | 'success'
const series: { key: SeriesKey; label: string; color: string }[] = [
  { key: 'requests', label: 'Запросы', color: '#8b95a5' },
  { key: 'created', label: 'Созданные', color: '#e0a13a' },
  { key: 'success', label: 'Успешные', color: '#3fbf6b' },
]

const yMax = computed(() => {
  let m = 0
  for (const p of props.points) m = Math.max(m, p.requests, p.created, p.success)
  return m > 0 ? m : 1
})

function xAt(i: number): number {
  const n = props.points.length
  if (n <= 1) return PAD_L + (W.value - PAD_L - PAD_R) / 2
  return PAD_L + ((W.value - PAD_L - PAD_R) * i) / (n - 1)
}
function yAt(v: number): number {
  return H - PAD_B - ((H - PAD_T - PAD_B) * v) / yMax.value
}
function linePoints(key: SeriesKey): string {
  return props.points.map((p, i) => `${xAt(i)},${yAt(p[key])}`).join(' ')
}

const yTicks = computed(() => {
  const ticks = 4
  return Array.from({ length: ticks + 1 }, (_, i) => {
    const value = Math.round((yMax.value * i) / ticks)
    return { value, y: yAt(value) }
  })
})

const xTicks = computed(() => {
  const n = props.points.length
  if (!n) return []
  const want = Math.min(7, n)
  const step = Math.max(1, Math.floor(n / want))
  const out: { i: number; label: string }[] = []
  for (let i = 0; i < n; i += step) out.push({ i, label: props.xLabels[i] ?? '' })
  return out
})

const hoverIdx = ref<number | null>(null)

function onMove(e: MouseEvent) {
  const n = props.points.length
  if (!n) return
  const rect = (e.currentTarget as SVGSVGElement).getBoundingClientRect()
  if (!rect.width) return
  const xView = ((e.clientX - rect.left) / rect.width) * W.value
  const frac = (xView - PAD_L) / (W.value - PAD_L - PAD_R)
  const idx = Math.round(frac * (n - 1))
  hoverIdx.value = Math.min(n - 1, Math.max(0, idx))
}

const tooltipStyle = computed(() => {
  if (hoverIdx.value === null) return {}
  const x = xAt(hoverIdx.value)
  const flip = x > W.value / 2
  return {
    top: '8px',
    left: `${x}px`,
    transform: flip ? 'translateX(calc(-100% - 8px))' : 'translateX(8px)',
  } as Record<string, string>
})
</script>
