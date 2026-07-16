<template>
  <span
    class="skeleton relative block overflow-hidden bg-bg-hover/70"
    :class="rounded ? roundedClass : ''"
    :style="style"
    aria-hidden="true"
  />
</template>

<script setup lang="ts">
import { computed } from 'vue'

// Generic loading placeholder. Renders a `<span class="block">` (block-flow
// element that doesn't pollute parent inline-baseline) with a horizontal
// shimmer sweep on top of a soft base tint. Width/height are passed as
// CSS values so callers can mix px, rem, %, etc. without leaking Tailwind
// classes into shape decisions.
//
// Defaults: full width × 1rem tall — the typical "one line of text" case.
const props = withDefaults(defineProps<{
  /** CSS width (e.g. "100%", "8rem", "120px"). Default: 100%. */
  width?: string
  /** CSS height (e.g. "1rem", "2.25rem", "120px"). Default: 1rem. */
  height?: string
  /** Apply rounded corners. Set to false for sharp rectangles. */
  rounded?: boolean
  /** Tailwind rounded utility — controls corner radius when rounded=true. */
  radius?: 'sm' | 'md' | 'lg' | 'xl' | '2xl' | 'full'
}>(), {
  width: '100%',
  height: '1rem',
  rounded: true,
  radius: 'md',
})

const style = computed(() => ({
  width: props.width,
  height: props.height,
}))

const roundedClass = computed(() => {
  switch (props.radius) {
    case 'sm': return 'rounded-sm'
    case 'md': return 'rounded-md'
    case 'lg': return 'rounded-lg'
    case 'xl': return 'rounded-xl'
    case '2xl': return 'rounded-2xl'
    case 'full': return 'rounded-full'
  }
})
</script>

<style scoped>
/* Shimmer sweep — a soft highlight band slides across the skeleton from
   left to right on a 1.4s loop. Plain `animate-pulse` (opacity oscillation)
   was too subtle on the dark theme; a moving gradient reads as "loading"
   at a glance and stays consistent across all skeleton sizes. The band is
   tinted with the global accent color at low alpha so it picks up the
   brand without screaming. */
.skeleton::after {
  content: '';
  position: absolute;
  inset: 0;
  background-image: linear-gradient(
    90deg,
    transparent 0%,
    rgba(255, 255, 255, 0.06) 40%,
    rgba(212, 164, 94, 0.18) 50%,
    rgba(255, 255, 255, 0.06) 60%,
    transparent 100%
  );
  transform: translateX(-100%);
  animation: skeleton-shimmer 1.4s ease-in-out infinite;
}

@keyframes skeleton-shimmer {
  0%   { transform: translateX(-100%); }
  100% { transform: translateX(100%); }
}

/* Respect user motion preferences — fall back to a static block (no pulse,
   no sweep) for visitors with reduced-motion enabled. */
@media (prefers-reduced-motion: reduce) {
  .skeleton::after {
    animation: none;
    display: none;
  }
}
</style>
