<template>
  <span ref="rootRef" class="relative inline-flex">
    <button
      ref="triggerRef"
      type="button"
      class="inline-flex h-4 w-4 items-center justify-center rounded-full text-text-muted transition hover:text-text-main focus:outline-none focus-visible:ring-2 focus-visible:ring-accent"
      :aria-label="ariaLabel || 'Подсказка'"
      :aria-expanded="open"
      @click.stop="toggle"
      @mouseenter="onHoverIn"
      @mouseleave="onHoverOut"
      @focus="onHoverIn"
      @blur="onHoverOut"
    >
      <HelpCircle class="h-3.5 w-3.5" />
    </button>
    <Teleport to="body">
      <Transition name="help">
        <div
          v-if="open"
          ref="tooltipRef"
          class="fixed z-[9999] w-max max-w-[260px] rounded-lg border border-border bg-bg-surface px-3 py-2 text-xs leading-snug text-text-secondary shadow-prime"
          :style="tooltipStyle"
          role="tooltip"
        >
          <slot>{{ text }}</slot>
        </div>
      </Transition>
    </Teleport>
  </span>
</template>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { onClickOutside } from '@vueuse/core'
import { HelpCircle } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  /** Tooltip body when no default slot is provided. */
  text?: string
  ariaLabel?: string
}>(), {})

const open = ref(false)
const rootRef = ref<HTMLElement | null>(null)
const triggerRef = ref<HTMLElement | null>(null)
const tooltipRef = ref<HTMLElement | null>(null)
const tooltipStyle = ref<Record<string, string>>({})

onClickOutside(rootRef, () => { open.value = false })

function toggle() {
  open.value = !open.value
}

function onHoverIn() {
  // Only react to real mouse hover — touch devices fire mouseenter on
  // tap and would race the click handler.
  if (isHoverDevice()) open.value = true
}

function onHoverOut() {
  if (isHoverDevice()) open.value = false
}

function isHoverDevice(): boolean {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(hover: hover)').matches
}

// ── Positioning ───────────────────────────────────────────────────
// Preferred: centered above the trigger. Flip to below when the top
// edge would clip; shift horizontally to keep the tooltip fully inside
// the viewport. Recomputed on every open / scroll / resize so the
// popup never goes off-screen.
const GAP = 6   // px between trigger and tooltip
const MARGIN = 8 // safe distance from viewport edges

function reposition() {
  const trigger = triggerRef.value
  const tooltip = tooltipRef.value
  if (!trigger || !tooltip) return

  const t = trigger.getBoundingClientRect()
  // Reset transform so width measurement is honest after a previous
  // shift; otherwise the previous offset bleeds into the new layout.
  tooltip.style.transform = ''
  const w = tooltip.offsetWidth
  const h = tooltip.offsetHeight
  const vw = window.innerWidth
  const vh = window.innerHeight

  // Vertical: try above, flip to below when top would clip.
  let top: number
  const spaceAbove = t.top
  if (spaceAbove >= h + GAP + MARGIN) {
    top = t.top - h - GAP
  } else {
    top = t.bottom + GAP
  }
  // Clamp vertically in case both sides are tight (very short viewport).
  top = Math.max(MARGIN, Math.min(top, vh - h - MARGIN))

  // Horizontal: prefer centered on the trigger, clamp to viewport.
  let left = t.left + t.width / 2 - w / 2
  left = Math.max(MARGIN, Math.min(left, vw - w - MARGIN))

  tooltipStyle.value = {
    top: `${top}px`,
    left: `${left}px`,
  }
}

watch(open, async (val) => {
  if (!val) return
  // Wait for the Teleport-ed node to mount, then measure & place.
  await nextTick()
  reposition()
})

function onWindowChange() {
  if (open.value) reposition()
}

if (typeof window !== 'undefined') {
  window.addEventListener('resize', onWindowChange)
  window.addEventListener('scroll', onWindowChange, true)  // capture: catch scrolls inside modals/lists
}

onBeforeUnmount(() => {
  if (typeof window !== 'undefined') {
    window.removeEventListener('resize', onWindowChange)
    window.removeEventListener('scroll', onWindowChange, true)
  }
})
</script>

<style scoped>
.help-enter-active, .help-leave-active {
  transition: opacity 0.12s ease, transform 0.12s ease;
}
.help-enter-from, .help-leave-to {
  opacity: 0;
  transform: translateY(-2px);
}
</style>
