<template>
  <Teleport to="body">
    <Transition name="modal" :duration="200">
      <div
        v-if="modelValue"
        class="fixed inset-0 flex overflow-y-auto"
        :class="mobileFullscreen
          ? 'items-stretch p-0 sm:items-center sm:justify-center sm:p-4'
          : 'items-center justify-center p-4'"
        :style="{ zIndex }"
        @click.self="$emit('update:modelValue', false)"
      >
        <div class="modal-backdrop fixed inset-0" @touchmove.prevent @wheel.prevent />
        <div
          class="modal-panel relative z-10 flex w-full flex-col bg-bg-surface shadow-[0_30px_90px_rgba(0,0,0,0.5)]"
          :class="[
            mobileFullscreen
              ? 'min-h-screen rounded-none border-0 sm:my-auto sm:min-h-0 sm:max-h-[calc(100dvh-2rem)] sm:rounded-2xl sm:border sm:border-border'
              : 'my-auto max-h-[calc(100dvh-2rem)] rounded-2xl border border-border',
            sizeClass,
          ]"
        >
          <!-- Custom header takes over the whole bar; default keeps the
               title + close button layout used by every existing modal. -->
          <slot v-if="$slots.header" name="header" :close="closeModal" />
          <div
            v-else
            class="flex shrink-0 items-center justify-between border-b border-border px-5 py-4"
          >
            <h3 class="text-lg font-bold text-text-main">{{ title }}</h3>
            <button
              type="button"
              class="flex h-8 w-8 items-center justify-center rounded-lg text-text-muted transition hover:bg-bg-hover hover:text-text-main"
              @click="closeModal"
            >
              <X class="h-5 w-5" />
            </button>
          </div>
          <div class="min-h-0 flex-1 overflow-y-auto p-5">
            <slot />
          </div>
          <!-- Footer keeps right-aligned action buttons by default; an
               optional ``footerLeft`` slot is pushed to the start via
               ``mr-auto`` so existing footer-only modals don't reflow. -->
          <div
            v-if="$slots.footer || $slots.footerLeft"
            class="flex shrink-0 items-center justify-end gap-3 border-t border-border px-5 py-4"
          >
            <div v-if="$slots.footerLeft" class="mr-auto">
              <slot name="footerLeft" />
            </div>
            <slot name="footer" />
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<script setup lang="ts">
import { computed, watch } from 'vue'
import { X } from 'lucide-vue-next'
import { useScrollLock } from '@vueuse/core'

const props = withDefaults(defineProps<{
  modelValue: boolean
  title: string
  size?: 'sm' | 'md' | 'lg' | 'xl'
  zIndex?: number
  /**
   * On screens below the ``sm`` breakpoint render the modal as a
   * full-screen sheet (no rounded corners, no border, fills the
   * viewport). Above ``sm`` falls back to the regular centered card.
   */
  mobileFullscreen?: boolean
}>(), {
  size: 'md',
  zIndex: 50,
  mobileFullscreen: false,
})

const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()

function closeModal() {
  emit('update:modelValue', false)
}

const isLocked = useScrollLock(typeof document !== 'undefined' ? document.body : null)

watch(() => props.modelValue, (val) => {
  isLocked.value = val
}, { immediate: true })

const sizeClass = computed(() => ({
  sm: 'max-w-md',
  md: 'max-w-xl',
  lg: 'max-w-3xl',
  xl: 'max-w-4xl',
}[props.size]))
</script>

<style scoped>
/* Animate the backdrop (dim + blur) and the panel (opacity) independently but
   over the SAME 0.2s, so they start and finish together. We deliberately do
   NOT animate opacity on their shared parent: an ancestor with opacity < 1
   suppresses the child's backdrop-filter until it reaches 1, so the blur
   "popped in" at the end (panel appeared first, dimming after). Driving the
   backdrop via background-color/backdrop-filter keeps it at opacity 1 the
   whole time, so the blur ramps smoothly in sync with the panel.
   :duration on <Transition> supplies the timing since the root element itself
   no longer transitions. */
.modal-backdrop {
  background-color: rgba(0, 0, 0, 0.6);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
}
.modal-enter-active .modal-backdrop,
.modal-leave-active .modal-backdrop {
  transition: background-color 0.2s ease, backdrop-filter 0.2s ease, -webkit-backdrop-filter 0.2s ease;
}
.modal-enter-from .modal-backdrop,
.modal-leave-to .modal-backdrop {
  background-color: rgba(0, 0, 0, 0);
  backdrop-filter: blur(0px);
  -webkit-backdrop-filter: blur(0px);
}
.modal-enter-active .modal-panel,
.modal-leave-active .modal-panel {
  transition: opacity 0.2s ease;
}
.modal-enter-from .modal-panel,
.modal-leave-to .modal-panel {
  opacity: 0;
}
</style>
