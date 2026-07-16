<template>
  <div class="rounded-2xl border border-border bg-panel-gradient p-5">
    <!-- Header: average turnover over the streak window · current level + bonus -->
    <div class="flex items-start justify-between gap-3">
      <div class="min-w-0">
        <div class="text-xs font-bold uppercase tracking-wider text-text-muted">
          Средн. оборот · {{ tier.window_days }} дн.
        </div>
        <div class="mt-1 truncate text-xl font-black text-text-main">
          {{ formatInt(tier.avg_volume_usdt) }}
          <span class="text-sm font-bold text-text-muted">USDT</span>
        </div>
      </div>
      <div class="shrink-0 text-right">
        <div class="text-xs font-bold uppercase tracking-wider text-text-muted">Уровень {{ currentLevel }}</div>
        <div
          class="mt-1 flex items-center justify-end gap-1 text-xl font-black"
          :class="!tier.locked && tier.current_percent > 0 ? 'text-accent' : 'text-text-muted'"
        >
          <Lock v-if="tier.locked" class="h-4 w-4" />
          +{{ formatNumber(tier.current_percent) }}%
        </div>
      </div>
    </div>

    <p v-if="tier.locked" class="mt-2 text-xs font-semibold text-text-muted">
      Бонус активируется, когда наберёте стрик
    </p>

    <!-- Adaptive stepper: one node per level (+ a base 0-node); dimmed while locked -->
    <div class="mt-5" :class="tier.locked ? 'opacity-50' : ''">
      <div class="flex items-center">
        <template v-for="(_node, i) in nodes" :key="i">
          <div
            class="relative z-10 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-xs font-black transition-colors"
            :class="nodeClass(i)"
          >
            {{ i }}
          </div>
          <div v-if="i < nodes.length - 1" class="relative h-1 flex-1 rounded-full bg-bg-hover">
            <div
              class="absolute inset-y-0 left-0 rounded-full bg-accent"
              :style="{ width: connectorFill(i) }"
            />
            <div
              v-if="i === currentLevel && dotFraction > 0 && dotFraction < 1"
              class="absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-accent bg-white shadow"
              :style="{ left: `${dotFraction * 100}%` }"
            />
          </div>
        </template>
      </div>

      <!-- Labels aligned under each node (same node/gap structure) -->
      <div class="mt-2 flex">
        <template v-for="(node, i) in nodes" :key="i">
          <div class="flex w-8 shrink-0 flex-col items-center whitespace-nowrap">
            <span class="text-[11px] font-bold text-text-secondary">{{ compact(node.threshold_usdt) }}</span>
            <span
              class="text-[11px] font-bold"
              :class="node.percent > 0 && i <= currentLevel ? 'text-accent' : 'text-text-muted'"
            >
              {{ node.percent > 0 ? '+' + formatNumber(node.percent) + '%' : '0%' }}
            </span>
          </div>
          <div v-if="i < nodes.length - 1" class="flex-1"></div>
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Lock } from 'lucide-vue-next'
import { formatInt, formatNumber } from '@/utils/format'
import type { TraderTierLevel, TraderTierProgress } from '@/types'

const props = defineProps<{ tier: TraderTierProgress }>()

// Prepend a synthetic base level (0 turnover, 0%) so the bar starts at "0".
const nodes = computed<TraderTierLevel[]>(() => [
  { threshold_usdt: 0, percent: 0 },
  ...props.tier.levels,
])

// The base node is index 0, so the reached level is the band index + 1.
const currentLevel = computed(() => Math.max(0, props.tier.current_index + 1))

// How far the average is from the current node to the next one (drives the dot).
const dotFraction = computed(() => {
  const cur = nodes.value[currentLevel.value]
  const next = nodes.value[currentLevel.value + 1]
  if (!next) return 1
  const span = next.threshold_usdt - cur.threshold_usdt
  if (span <= 0) return 0
  return Math.min(1, Math.max(0, (props.tier.avg_volume_usdt - cur.threshold_usdt) / span))
})

function nodeClass(i: number): string {
  if (i === currentLevel.value) return 'bg-accent text-bg-main ring-2 ring-white'
  if (i < currentLevel.value) return 'bg-accent text-bg-main'
  return 'bg-bg-hover text-text-muted'
}

function connectorFill(i: number): string {
  if (i < currentLevel.value) return '100%'
  if (i === currentLevel.value) return `${dotFraction.value * 100}%`
  return '0%'
}

function compact(n: number): string {
  if (n >= 1e6) return `$${trimZero(n / 1e6)}M`
  if (n >= 1e3) return `$${trimZero(n / 1e3)}k`
  return `$${Math.round(n)}`
}

function trimZero(x: number): string {
  return String(Math.round(x * 10) / 10)
}
</script>
