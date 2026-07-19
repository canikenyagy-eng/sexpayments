<template>
  <span
    :class="[
      'inline-flex items-center rounded-lg border px-2.5 py-1 text-[11px] font-black uppercase tracking-[0.08em]',
      colorClasses,
    ]"
  >
    <slot />
  </span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  color?: 'success' | 'danger' | 'warning' | 'info' | 'default' | 'gold'
}>(), {
  color: 'default',
})

const colorClasses = computed(() => ({
  success: 'border-status-success/25 bg-status-success/10 text-status-success',
  danger: 'border-status-danger/25 bg-status-danger/10 text-status-danger',
  warning: 'border-status-warning/25 bg-status-warning/10 text-status-warning',
  info: 'border-status-info/25 bg-status-info/10 text-status-info',
  default: 'border-accent/15 bg-bg-hover/55 text-text-secondary',
  gold: 'border-accent/25 bg-accent/10 text-accent',
}[props.color]))
</script>
