<template>
  <div class="dashboard-card relative flex flex-col justify-between overflow-hidden">
    <div class="pointer-events-none absolute inset-x-4 top-0 h-px bg-gradient-to-r from-transparent via-accent/30 to-transparent" />
    <div class="mb-4 flex items-center justify-between gap-3">
      <span class="text-[11px] font-black uppercase tracking-[0.14em] text-text-muted">{{ label }}</span>
      <span v-if="icon" class="grid h-8 w-8 shrink-0 place-items-center rounded-lg border border-accent/15 bg-bg-main/45 text-accent">
        <component :is="icon" class="h-4 w-4" />
      </span>
    </div>
    <BaseSkeleton v-if="loading" height="2rem" width="60%" />
    <div v-else class="break-words text-2xl font-black leading-none text-text-main">{{ value }}</div>
    <BaseSkeleton v-if="loading && subtitle" class="mt-1" height="0.75rem" width="40%" />
    <p v-else-if="subtitle" class="mt-2 text-xs font-semibold leading-5 text-text-muted">{{ subtitle }}</p>
  </div>
</template>

<script setup lang="ts">
import type { Component } from 'vue'
import BaseSkeleton from './BaseSkeleton.vue'

withDefaults(defineProps<{
  label: string
  value: string | number
  subtitle?: string
  icon?: Component | string
  loading?: boolean
}>(), {
  loading: false,
})
</script>
