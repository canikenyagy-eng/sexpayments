<template>
  <section
    class="sp-panel-grid relative mb-7 overflow-hidden rounded-[1.4rem] border border-accent/15 bg-bg-surface/65 p-5 shadow-[0_28px_90px_rgba(0,0,0,0.38),inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-2xl sm:p-7 lg:p-8"
  >
    <div class="pointer-events-none absolute inset-0" :class="tone.overlay" />
    <div class="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/50 to-transparent" />
    <div class="pointer-events-none absolute bottom-0 left-0 h-px w-full bg-gradient-to-r from-accent-dark/0 via-accent-dark/45 to-accent-dark/0" />

    <div class="relative grid gap-7 xl:grid-cols-[minmax(0,0.92fr)_minmax(360px,0.58fr)] xl:items-end">
      <div>
        <div class="mb-5 inline-flex max-w-full items-center gap-2 overflow-hidden rounded-xl border border-accent/20 bg-bg-main/55 px-3 py-2 text-[10px] font-black uppercase tracking-[0.12em] text-accent sm:text-xs sm:tracking-[0.18em]">
          <component :is="tone.icon" class="h-4 w-4" />
          <span class="min-w-0 truncate">{{ eyebrow }}</span>
        </div>
        <h1 class="max-w-4xl text-[clamp(2rem,4vw,4.4rem)] font-black leading-[0.98] text-text-main">
          {{ title }}
        </h1>
        <p class="mt-5 max-w-2xl text-base font-semibold leading-7 text-text-secondary sm:text-lg">
          {{ subtitle }}
        </p>
        <div v-if="$slots.actions" class="mt-6 flex flex-wrap items-center gap-3">
          <slot name="actions" />
        </div>
      </div>

      <div class="grid gap-3 sm:grid-cols-2">
        <div
          v-for="metric in metrics"
          :key="metric.label"
          class="min-h-[116px] rounded-[1rem] border border-accent/15 bg-bg-main/60 p-4 shadow-[inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl"
        >
          <span class="mb-3 block text-[11px] font-black uppercase tracking-[0.14em] text-text-muted">
            {{ metric.label }}
          </span>
          <strong class="block break-words text-2xl font-black leading-none text-text-main">
            {{ metric.value }}
          </strong>
          <p v-if="metric.caption" class="mt-3 text-xs font-semibold leading-5 text-text-muted">
            {{ metric.caption }}
          </p>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { Component } from 'vue'
import { Activity, Network, Store } from 'lucide-vue-next'
import type { UserRole } from '@/types'

interface HeroMetric {
  label: string
  value: string | number
  caption?: string
}

const props = defineProps<{
  role: Extract<UserRole, 'merchant' | 'trader' | 'teamlead'>
  eyebrow: string
  title: string
  subtitle: string
  metrics: HeroMetric[]
}>()

const tone = computed<{
  icon: Component
  overlay: string
}>(() => {
  if (props.role === 'merchant') {
    return {
      icon: Store,
      overlay: 'bg-[linear-gradient(135deg,rgba(139,21,56,0.22),rgba(23,23,23,0.08)_52%,rgba(214,163,143,0.08))]',
    }
  }
  if (props.role === 'trader') {
    return {
      icon: Activity,
      overlay: 'bg-[linear-gradient(135deg,rgba(38,30,28,0.92),rgba(23,23,23,0.24)_54%,rgba(214,163,143,0.12))]',
    }
  }
  return {
    icon: Network,
    overlay: 'bg-[linear-gradient(135deg,rgba(36,29,31,0.88),rgba(139,21,56,0.18)_58%,rgba(13,13,13,0.16))]',
  }
})
</script>
