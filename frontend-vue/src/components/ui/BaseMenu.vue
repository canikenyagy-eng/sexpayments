<template>
  <div ref="rootRef" class="relative inline-block">
    <button
      type="button"
      :class="triggerClass || defaultTriggerClass"
      :aria-expanded="open"
      :aria-haspopup="true"
      @click="toggle"
    >
      <slot name="trigger">
        <MoreHorizontal class="h-5 w-5" />
      </slot>
    </button>
    <Transition name="menu">
      <div
        v-if="open"
        class="absolute z-50 mt-1 min-w-[220px] overflow-hidden rounded-xl border border-border bg-bg-surface py-1 shadow-prime"
        :class="alignClass"
        @click="open = false"
      >
        <slot />
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { onClickOutside } from '@vueuse/core'
import { MoreHorizontal } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  /** Position of the popup relative to the trigger. */
  align?: 'left' | 'right'
  /** Override the default trigger button styling. */
  triggerClass?: string
}>(), {
  align: 'right',
})

const open = ref(false)
const rootRef = ref<HTMLElement | null>(null)

onClickOutside(rootRef, () => {
  open.value = false
})

function toggle() {
  open.value = !open.value
}

const defaultTriggerClass =
  'flex h-8 w-8 items-center justify-center rounded-lg text-text-muted transition hover:bg-bg-hover hover:text-text-main'

const alignClass = computed(() =>
  props.align === 'left' ? 'left-0' : 'right-0',
)

defineExpose({ close: () => { open.value = false } })
</script>

<style scoped>
.menu-enter-active, .menu-leave-active {
  transition: opacity 0.12s ease, transform 0.12s ease;
}
.menu-enter-from, .menu-leave-to {
  opacity: 0;
  transform: translateY(-2px);
}
</style>
