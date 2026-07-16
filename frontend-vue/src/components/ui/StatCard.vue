<template>
  <div class="dashboard-card flex flex-col justify-between">
    <div class="mb-3 flex items-center justify-between">
      <span class="text-xs font-bold uppercase tracking-wider text-text-muted">{{ label }}</span>
      <component v-if="icon" :is="icon" class="h-5 w-5 text-text-muted" />
    </div>
    <BaseSkeleton v-if="loading" height="2rem" width="60%" />
    <div v-else class="text-2xl font-black text-text-main">{{ value }}</div>
    <BaseSkeleton v-if="loading && subtitle" class="mt-1" height="0.75rem" width="40%" />
    <p v-else-if="subtitle" class="mt-1 text-xs text-text-muted">{{ subtitle }}</p>
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
