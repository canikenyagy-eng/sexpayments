<template>
  <span
    v-if="source"
    :class="[
      'inline-flex items-center gap-1 rounded-lg border border-border/70 bg-bg-surface/80 font-bold uppercase tracking-wider text-text-main',
      sizeClass,
    ]"
  >
    <img
      v-if="iconSrc"
      :src="iconSrc"
      :alt="label"
      :class="iconBoxClass"
      class="shrink-0"
    />
    <span>{{ label }}</span>
  </span>
  <span v-else class="text-text-muted">—</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(defineProps<{
  source?: string | null
  size?: 'sm' | 'md'
}>(), {
  size: 'sm',
})

const sizeClass = computed(() =>
  props.size === 'md' ? 'px-3 py-1 text-xs' : 'px-2 py-0.5 text-[11px]',
)

const iconBoxClass = computed(() =>
  props.size === 'md' ? 'h-3.5 w-3.5' : 'h-3 w-3',
)

const normalized = computed(() => (props.source ?? '').toString().toLowerCase())

const label = computed(() => normalized.value.toUpperCase())

const iconSrc = computed<string | null>(() => {
  if (normalized.value === 'rapira') return '/logos/rapira.svg'
  return null
})
</script>
