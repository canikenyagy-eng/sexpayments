<template>
  <!--
    Small inline badge for cascade-provider rows and dropdown options.
    Mirrors the visual style of RateSourceBadge so admin views stay
    consistent: rounded border + uppercase label + optional icon on the
    left. Icon is sourced from ``public/logos/<filename>`` when the
    adapter shipped a logo via its ``LOGO_FILENAME`` class-attr; without
    one we just render the text (the requirement is "no logo = no logo").
  -->
  <span
    v-if="label"
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
import type { CascadeAdapterInfo } from '@/types'

const props = withDefaults(defineProps<{
  // Pass the whole adapter when it's at hand — gives us both display_name
  // and logo_filename. If only the bare code is known (e.g. adapter list
  // hasn't loaded yet), fall back to the ``code`` prop.
  adapter?: CascadeAdapterInfo | null
  code?: string | null
  text?: string | null
  size?: 'sm' | 'md'
}>(), {
  size: 'sm',
  adapter: null,
  code: null,
  text: null,
})

const sizeClass = computed(() =>
  props.size === 'md' ? 'px-3 py-1 text-xs' : 'px-2 py-0.5 text-[11px]',
)

const iconBoxClass = computed(() =>
  props.size === 'md' ? 'h-3.5 w-3.5' : 'h-3 w-3',
)

const label = computed<string>(() => {
  if (props.text) return props.text.toUpperCase()
  if (props.adapter?.display_name) return props.adapter.display_name.toUpperCase()
  if (props.adapter?.code) return props.adapter.code.toUpperCase()
  if (props.code) return props.code.toUpperCase()
  return ''
})

// Resolve the logo URL via the public-folder convention. Vite serves
// everything under ``public/`` from the site root, so a class-attr
// ``"swifty.svg"`` on the adapter maps to ``/logos/swifty.svg``.
const iconSrc = computed<string | null>(() => {
  const fname = props.adapter?.logo_filename
  return fname ? `/logos/${fname}` : null
})
</script>
