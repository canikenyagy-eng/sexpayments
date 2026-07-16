<template>
  <div>
    <div class="mb-3 flex flex-wrap items-center gap-3 text-xs">
      <span class="inline-flex items-center gap-1.5">
        <span class="inline-block h-2.5 w-2.5 rounded-full" :style="{ background: TURNOVER_COLOR }" />
        <span class="text-text-muted">Оборот</span>
        <span class="font-mono font-bold text-text-main">{{ formatInt(totals.turnover) }}</span>
      </span>
      <span class="inline-flex items-center gap-1.5">
        <span class="inline-block h-2.5 w-2.5 rounded-full" :style="{ background: PROVIDER_COLOR }" />
        <span class="text-text-muted">Оборот каскада</span>
        <span class="font-mono font-bold text-text-main">{{ formatInt(totals.providerTurnover) }}</span>
      </span>
      <span class="inline-flex items-center gap-1.5">
        <span class="inline-block h-2.5 w-2.5 rounded-full" :style="{ background: PROFIT_COLOR }" />
        <span class="text-text-muted">Прибыль</span>
        <span class="font-mono font-bold text-text-main">{{ formatInt(totals.profit) }}</span>
      </span>
      <span class="inline-flex items-center gap-1.5">
        <span class="inline-block h-2.5 w-2.5 rounded-full" :style="{ background: CASCADE_PROFIT_COLOR }" />
        <span class="text-text-muted">Прибыль каскада</span>
        <span class="font-mono font-bold text-text-main">{{ formatInt(totals.providerProfit) }}</span>
      </span>
    </div>

    <BaseLineChart
      :series="series"
      :x-labels="xLabels"
      :format-value="formatInt"
      :aspect-ratio="aspectRatio"
      :min-height="minHeight"
      :max-height="maxHeight"
      :loading="loading"
      empty-text="Нет данных за выбранный период"
    >
      <template #tooltip="{ index }">
        <div v-if="points[index]">
          <div class="mb-1 flex items-center gap-1.5 text-text-muted">
            <span>{{ xLabels[index] }}</span>
            <span v-if="weekdayLabels[index]" class="font-bold text-text-secondary">
              {{ weekdayLabels[index] }}
            </span>
          </div>
          <div class="flex items-center gap-1.5">
            <span class="inline-block h-2 w-2 rounded-full" :style="{ background: TURNOVER_COLOR }" />
            <span class="text-text-muted">Оборот:</span>
            <span class="font-mono font-bold text-text-main">{{ formatInt(points[index].turnover_usdt) }}</span>
            <DeltaBadge :delta="turnoverDeltas[index]" class="ml-auto" />
          </div>
          <div class="flex items-center gap-1.5">
            <span class="inline-block h-2 w-2 rounded-full" :style="{ background: PROVIDER_COLOR }" />
            <span class="text-text-muted">Каскад:</span>
            <span class="font-mono font-bold text-text-main">{{ formatInt(points[index].provider_turnover_usdt) }}</span>
            <DeltaBadge :delta="providerDeltas[index]" class="ml-auto" />
          </div>
          <div class="flex items-center gap-1.5">
            <span class="inline-block h-2 w-2 rounded-full" :style="{ background: PROFIT_COLOR }" />
            <span class="text-text-muted">Прибыль:</span>
            <span class="font-mono font-bold text-text-main">{{ formatInt(points[index].profit_usdt) }}</span>
            <DeltaBadge :delta="profitDeltas[index]" class="ml-auto" />
          </div>
          <div class="flex items-center gap-1.5">
            <span class="inline-block h-2 w-2 rounded-full" :style="{ background: CASCADE_PROFIT_COLOR }" />
            <span class="text-text-muted">Каскад:</span>
            <span class="font-mono font-bold text-text-main">{{ formatInt(points[index].provider_profit_usdt) }}</span>
            <DeltaBadge :delta="providerProfitDeltas[index]" class="ml-auto" />
          </div>
          <div class="text-text-muted">
            <div>Сделок:</div>
            <div class="flex items-center gap-1.5 pl-2">
              <span>Всего {{ points[index].orders }}</span>
              <DeltaBadge :delta="ordersDeltas[index]" class="ml-auto" />
            </div>
            <div v-if="points[index].provider_orders" class="flex items-center gap-1.5 pl-2">
              <span>Через каскад {{ points[index].provider_orders }}</span>
              <DeltaBadge :delta="providerOrdersDeltas[index]" class="ml-auto" />
            </div>
          </div>
        </div>
      </template>
    </BaseLineChart>
  </div>
</template>

<script setup lang="ts">
import { computed, h, ref, onMounted, onBeforeUnmount } from 'vue'
import dayjs from 'dayjs'
import { formatInt } from '@/utils/format'
import BaseLineChart, { type ChartSeries } from '@/components/ui/BaseLineChart.vue'
import type { TimeseriesPoint } from '@/api/services/stats.service'

// Inline mini-компонент: цветная подпись «±N.N%» под каждой метрикой.
// ``delta = null`` — пропуск (первая точка / предыдущее значение = 0).
const DeltaBadge = {
  // No type assertion: Vue prop validation rejects `null` when type=Number,
  // and `delta` is legitimately null for the first point. Loose declaration
  // is fine here — the inline component is only consumed in one place.
  props: { delta: { default: null } },
  setup(props: { delta: number | null }) {
    return () => {
      if (props.delta === null || props.delta === undefined) {
        return h('span', { class: 'text-xs text-text-muted' }, '—')
      }
      const value = props.delta
      const sign = value > 0 ? '+' : ''
      const cls =
        value > 0
          ? 'text-xs font-semibold text-green-500'
          : value < 0
            ? 'text-xs font-semibold text-red-500'
            : 'text-xs text-text-muted'
      return h('span', { class: cls }, `${sign}${value.toFixed(1)}%`)
    }
  },
}

const props = withDefaults(defineProps<{
  points: TimeseriesPoint[]
  granularity: '3hour' | 'hour' | 'day' | 'week' | 'month'
  loading?: boolean
}>(), {
  loading: false,
})

const TURNOVER_COLOR = '#d4a45e'
const PROFIT_COLOR = '#22c55e'
// Distinct enough from the gold turnover line yet still warm — keeps the
// chart visually coherent and lets the provider slice read as a subset
// of the gold trend at a glance.
const PROVIDER_COLOR = '#60a5fa'
// Cascade profit = cascade (blue) ∩ profit (green) → teal; reads as the
// cascade subset of the green profit line.
const CASCADE_PROFIT_COLOR = '#2dd4bf'

// Responsive height — на широких экранах высота растёт, на мобилке падает
// до minHeight, но не «сжимается» до полоски: aspect 2.6 даёт сбалансированный
// прямоугольник (1300×500, 800×308, на мобилке достигаем minHeight).
const isMobile = ref(false)
function syncMobile() {
  if (typeof window !== 'undefined') {
    isMobile.value = window.innerWidth < 640
  }
}
onMounted(() => {
  syncMobile()
  window.addEventListener('resize', syncMobile)
})
onBeforeUnmount(() => window.removeEventListener('resize', syncMobile))

const aspectRatio = 2.6
const minHeight = computed(() => (isMobile.value ? 260 : 300))
const maxHeight = computed(() => (isMobile.value ? 380 : 520))

const totals = computed(() => ({
  turnover: props.points.reduce((s, p) => s + p.turnover_usdt, 0),
  profit: props.points.reduce((s, p) => s + p.profit_usdt, 0),
  providerTurnover: props.points.reduce((s, p) => s + (p.provider_turnover_usdt || 0), 0),
  providerProfit: props.points.reduce((s, p) => s + (p.provider_profit_usdt || 0), 0),
}))

// Процент изменения каждой точки относительно предыдущей. Для первой
// точки и для случая prev=0 возвращаем null (badge покажет «—»), чтобы не
// делить на ноль и не показывать бессмысленные ∞%.
function buildDeltas(values: number[]): (number | null)[] {
  return values.map((v, i) => {
    if (i === 0) return null
    const prev = values[i - 1]
    if (!prev) return null
    return ((v - prev) / prev) * 100
  })
}
const turnoverDeltas = computed(() => buildDeltas(props.points.map(p => p.turnover_usdt)))
const profitDeltas = computed(() => buildDeltas(props.points.map(p => p.profit_usdt)))
const ordersDeltas = computed(() => buildDeltas(props.points.map(p => p.orders)))
const providerDeltas = computed(() => buildDeltas(props.points.map(p => p.provider_turnover_usdt || 0)))
const providerOrdersDeltas = computed(() => buildDeltas(props.points.map(p => p.provider_orders || 0)))
const providerProfitDeltas = computed(() => buildDeltas(props.points.map(p => p.provider_profit_usdt || 0)))

function pointLabel(p: TimeseriesPoint): string {
  const d = dayjs.unix(p.ts)
  switch (props.granularity) {
    case 'hour': return d.format('DD.MM HH:mm')
    case '3hour': return d.format('DD.MM HH:mm')  // bucket start (00:00 / 03:00 / …)
    case 'day': return d.format('DD.MM')
    case 'week': return d.format('DD.MM') + '+'
    case 'month': return d.format('MM.YYYY')
  }
}

const xLabels = computed(() => props.points.map(pointLabel))

const WEEKDAYS_RU = ['ВС', 'ПН', 'ВТ', 'СР', 'ЧТ', 'ПТ', 'СБ']
const weekdayLabels = computed(() =>
  props.points.map((p) =>
    props.granularity === 'day' || props.granularity === 'hour' || props.granularity === '3hour'
      ? WEEKDAYS_RU[dayjs.unix(p.ts).day()]
      : '',
  ),
)

const series = computed<ChartSeries[]>(() => [
  {
    name: 'Оборот',
    color: TURNOVER_COLOR,
    values: props.points.map(p => p.turnover_usdt),
    showArea: true,
  },
  {
    name: 'Оборот каскада',
    color: PROVIDER_COLOR,
    values: props.points.map(p => p.provider_turnover_usdt || 0),
  },
  {
    name: 'Прибыль',
    color: PROFIT_COLOR,
    values: props.points.map(p => p.profit_usdt),
  },
  {
    name: 'Прибыль каскада',
    color: CASCADE_PROFIT_COLOR,
    values: props.points.map(p => p.provider_profit_usdt || 0),
  },
])
</script>
