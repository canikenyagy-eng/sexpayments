<template>
  <span
    :class="[
      'inline-flex items-center rounded-lg px-2.5 py-1 text-xs font-bold',
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
  success: 'bg-status-success/15 text-status-success',
  danger: 'bg-status-danger/15 text-status-danger',
  warning: 'bg-status-warning/15 text-status-warning',
  info: 'bg-status-info/15 text-status-info',
  default: 'bg-bg-hover text-text-secondary',
  gold: 'bg-accent/15 text-accent',
}[props.color]))
</script>
