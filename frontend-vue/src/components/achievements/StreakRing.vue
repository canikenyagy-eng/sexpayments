<template>
  <div class="rounded-2xl border border-border bg-panel-gradient p-5">
    <div class="text-xs font-bold uppercase tracking-wider text-text-muted">Стрик</div>

    <!-- Adaptive segmented ring: one segment per required day, filled up to the streak -->
    <div class="relative mx-auto mt-3 h-40 w-40">
      <svg class="h-full w-full -rotate-90" viewBox="0 0 36 36">
        <circle
          v-for="seg in segments"
          :key="seg"
          cx="18"
          cy="18"
          r="15.915"
          fill="none"
          stroke-width="3"
          stroke-linecap="round"
          class="transition-colors"
          :class="seg - 1 < filled ? 'stroke-accent' : 'stroke-bg-hover'"
          :stroke-dasharray="`${dash} ${100 - dash}`"
          :stroke-dashoffset="-((seg - 1) * segLen)"
        />
      </svg>
      <div class="absolute inset-0 flex flex-col items-center justify-center">
        <div class="text-4xl font-black leading-none text-text-main">
          {{ streak.current_days }}<span class="text-xl text-text-muted">/{{ streak.target_days }}</span>
        </div>
        <div class="mt-1 text-xs font-bold uppercase tracking-wider text-text-muted">дней подряд</div>
      </div>
    </div>

    <div class="mt-4 flex justify-center">
      <div class="rounded-xl border border-border bg-bg-card px-4 py-2 text-center text-sm font-bold text-text-secondary">
        <template v-if="streak.active">
          Бонус <span class="text-accent">+{{ formatNumber(streak.bonus_percent) }}%</span> активен
        </template>
        <template v-else-if="streak.bonus_percent > 0">
          Ещё {{ daysLeft }} {{ daysWord }} до <span class="text-accent">+{{ formatNumber(streak.bonus_percent) }}%</span>
        </template>
        <template v-else>
          Наберите оборот и стрик, чтобы разблокировать бонус
        </template>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { formatNumber } from '@/utils/format'
import type { TraderStreakProgress } from '@/types'

const props = defineProps<{ streak: TraderStreakProgress }>()

const segments = computed(() => Math.max(1, props.streak.target_days))
const filled = computed(() => Math.min(props.streak.current_days, segments.value))
const daysLeft = computed(() => Math.max(0, props.streak.target_days - props.streak.current_days))
const daysWord = computed(() => {
  const n = daysLeft.value
  if (n % 10 === 1 && n % 100 !== 11) return 'день'
  if ([2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100)) return 'дня'
  return 'дней'
})

// Circle is r=15.915 → circumference 100, so one segment spans 100/segments units.
const segLen = computed(() => 100 / segments.value)
const dash = computed(() => {
  const gap = Math.min(6, segLen.value * 0.4)
  return Math.max(0.5, segLen.value - gap)
})
</script>
