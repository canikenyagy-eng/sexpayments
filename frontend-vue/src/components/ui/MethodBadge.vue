<template>
  <span
    v-if="method"
    :class="[
      'inline-flex shrink-0 items-center gap-1 whitespace-nowrap rounded-lg border border-border/70 bg-bg-surface/80 font-bold uppercase tracking-wider text-text-main',
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
    <component
      v-else-if="iconComponent"
      :is="iconComponent"
      :class="iconBoxClass"
      class="shrink-0"
    />
    <span>{{ label }}</span>
  </span>
  <span v-else class="text-text-muted">—</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { CreditCard, Smartphone } from 'lucide-vue-next'
import { publicAsset } from '@/utils/assets'

const props = withDefaults(defineProps<{
  method?: string | null
  size?: 'sm' | 'md'
}>(), {
  size: 'sm',
})

// Slightly more right padding than left compensates for the `tracking-wider`
// trailing letter-spacing (uppercase + bold + tracking visually crowds the
// right edge against the border in tight cells).
const sizeClass = computed(() =>
  props.size === 'md' ? 'pl-3 pr-3.5 py-1 text-xs' : 'pl-2 pr-2.5 py-0.5 text-[11px]',
)

const iconBoxClass = computed(() =>
  props.size === 'md' ? 'h-3.5 w-3.5' : 'h-3 w-3',
)

const normalized = computed(() => (props.method ?? '').toString().toLowerCase())

const label = computed(() => normalized.value.toUpperCase())

const iconSrc = computed<string | null>(() => {
  if (normalized.value === 'sbp') return publicAsset('banks/sbp.svg')
  return null
})

const iconComponent = computed(() => {
  switch (normalized.value) {
    case 'card':
      return CreditCard
    case 'sim':
      return Smartphone
    default:
      return null
  }
})
</script>
