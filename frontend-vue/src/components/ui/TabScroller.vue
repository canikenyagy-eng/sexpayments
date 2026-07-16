<!--
  Horizontal scroll container for a row of tabs (Material-style).

  Wraps any inline row (pass it as the default slot — typically a single
  ``flex`` strip) and shows left/right chevron buttons ONLY when the content
  overflows in that direction. Clicking a chevron scrolls ~80% of the visible
  width; the native scrollbar is hidden (``no-scrollbar``) so touch/trackpad
  swipe still works. Arrow visibility is recomputed on scroll, on container
  resize, and on content resize (tabs added/removed) via ResizeObserver.

  Generic on purpose — it knows nothing about tabs, only about scrolling its
  slot. ``BaseTabs`` uses it so every tab group gets the scroller for free.
-->
<template>
  <div class="relative flex items-stretch">
    <button
      v-if="canLeft"
      type="button"
      aria-label="Прокрутить влево"
      class="z-10 flex shrink-0 items-center justify-center px-1 text-text-muted transition-colors hover:text-text-main"
      @click="scrollByStep(-1)"
    >
      <ChevronLeft class="h-5 w-5" />
    </button>

    <div
      ref="scrollerEl"
      class="min-w-0 flex-1 overflow-x-auto overflow-y-hidden no-scrollbar"
      @scroll="updateArrows"
    >
      <slot />
    </div>

    <button
      v-if="canRight"
      type="button"
      aria-label="Прокрутить вправо"
      class="z-10 flex shrink-0 items-center justify-center px-1 text-text-muted transition-colors hover:text-text-main"
      @click="scrollByStep(1)"
    >
      <ChevronRight class="h-5 w-5" />
    </button>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, nextTick, ref } from 'vue'
import { ChevronLeft, ChevronRight } from 'lucide-vue-next'

const scrollerEl = ref<HTMLElement | null>(null)
const canLeft = ref(false)
const canRight = ref(false)

const EPS = 2 // px tolerance for sub-pixel rounding

function updateArrows() {
  const el = scrollerEl.value
  if (!el) return
  canLeft.value = el.scrollLeft > EPS
  canRight.value = el.scrollLeft + el.clientWidth < el.scrollWidth - EPS
}

function scrollByStep(dir: 1 | -1) {
  const el = scrollerEl.value
  if (!el) return
  el.scrollBy({ left: dir * el.clientWidth * 0.8, behavior: 'smooth' })
}

let ro: ResizeObserver | null = null

onMounted(async () => {
  await nextTick()
  updateArrows()
  const el = scrollerEl.value
  if (el && typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => updateArrows())
    ro.observe(el) // container resize (window / layout)
    if (el.firstElementChild) ro.observe(el.firstElementChild) // content resize (tabs added/removed)
  }
  window.addEventListener('resize', updateArrows)
})

onBeforeUnmount(() => {
  ro?.disconnect()
  window.removeEventListener('resize', updateArrows)
})

// Lets a parent force a recompute (e.g. after async tabs load).
defineExpose({ update: updateArrows })
</script>
