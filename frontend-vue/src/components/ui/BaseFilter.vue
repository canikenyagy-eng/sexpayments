<template>
  <div class="relative" ref="wrapperRef">
    <div class="flex items-center gap-2">
      <button
        type="button"
        class="inline-flex items-center gap-2 rounded-xl border border-border bg-bg-hover px-3 py-2 text-sm font-bold text-text-main transition-all hover:bg-bg-card"
        @click="toggleOpen"
      >
        <SlidersHorizontal class="h-4 w-4 text-text-muted" />
        Фильтры
        <span
          v-if="activeCount && activeCount > 0"
          class="flex h-5 min-w-5 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-black text-bg-main"
        >
          {{ activeCount }}
        </span>
      </button>
      <button
        v-if="activeCount && activeCount > 0"
        type="button"
        class="inline-flex items-center gap-1 rounded-xl px-2.5 py-2 text-xs font-bold text-text-muted transition hover:text-status-danger"
        @click="$emit('reset')"
      >
        <X class="h-3.5 w-3.5" />
        Сброс
      </button>
    </div>

    <Transition
      enter-active-class="transition duration-150 ease-out"
      enter-from-class="scale-95 opacity-0"
      enter-to-class="scale-100 opacity-100"
      leave-active-class="transition duration-100 ease-in"
      leave-from-class="scale-100 opacity-100"
      leave-to-class="scale-95 opacity-0"
    >
      <div
        v-if="open"
        class="absolute top-full z-50 mt-2 w-[min(320px,calc(100vw-2rem))] max-w-[calc(100vw-2rem)] rounded-2xl border border-border bg-bg-surface p-4 shadow-xl"
        :class="alignRight ? 'right-0' : 'left-0'"
      >
        <div class="mb-3 flex items-center justify-between">
          <span class="text-xs font-bold uppercase tracking-widest text-text-muted">Фильтры</span>
          <button
            type="button"
            class="rounded-lg p-1 text-text-muted transition hover:bg-bg-hover hover:text-text-main"
            @click="open = false"
          >
            <X class="h-4 w-4" />
          </button>
        </div>

        <div class="space-y-3">
          <slot />
        </div>

        <div class="mt-4 flex items-center gap-2">
          <button
            type="button"
            class="flex-1 rounded-xl bg-gold-gradient px-3 py-2 text-sm font-bold text-text-main shadow-prime transition hover:-translate-y-px"
            @click="apply"
          >
            Применить
          </button>
          <button
            v-if="activeCount && activeCount > 0"
            type="button"
            class="rounded-xl px-3 py-2 text-sm font-bold text-text-muted transition hover:bg-bg-hover hover:text-text-main"
            @click="$emit('reset'); open = false"
          >
            Сброс
          </button>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { SlidersHorizontal, X } from 'lucide-vue-next'

defineProps<{
  activeCount?: number
}>()

const emit = defineEmits<{
  apply: []
  reset: []
}>()

const open = ref(false)
const alignRight = ref(false)
const wrapperRef = ref<HTMLElement | null>(null)

function updateAlignment() {
  const wrapper = wrapperRef.value
  if (!wrapper) return
  const rect = wrapper.getBoundingClientRect()
  const panelWidth = Math.min(320, window.innerWidth - 32)
  const margin = 16
  alignRight.value = rect.left + panelWidth + margin > window.innerWidth
}

async function toggleOpen() {
  if (open.value) {
    open.value = false
    return
  }
  updateAlignment()
  open.value = true
  await nextTick()
  updateAlignment()
}

function apply() {
  emit('apply')
  open.value = false
}

function onClickOutside(e: MouseEvent) {
  if (wrapperRef.value && !wrapperRef.value.contains(e.target as Node)) {
    open.value = false
  }
}

function onResize() {
  if (open.value) updateAlignment()
}

onMounted(() => {
  document.addEventListener('mousedown', onClickOutside)
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onClickOutside)
  window.removeEventListener('resize', onResize)
})
</script>
