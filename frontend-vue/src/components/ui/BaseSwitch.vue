<template>
  <button
    type="button"
    role="switch"
    :aria-checked="modelValue"
    :disabled="disabled || loading"
    :title="title"
    :class="[
      'relative inline-block shrink-0 rounded-full transition-colors duration-200',
      'focus:outline-none focus-visible:ring-2 focus-visible:ring-accent focus-visible:ring-offset-2 focus-visible:ring-offset-bg-main',
      sizeClasses.track,
      modelValue ? 'bg-accent' : 'bg-border',
      (disabled || loading) ? 'cursor-not-allowed opacity-50' : 'cursor-pointer',
    ]"
    @click.stop="toggle"
  >
    <span
      :class="[
        'absolute top-0.5 left-0.5 inline-flex items-center justify-center rounded-full bg-bg-main shadow transition-transform duration-200',
        sizeClasses.thumb,
        modelValue ? sizeClasses.translate : 'translate-x-0',
      ]"
    >
      <span
        v-if="loading"
        class="h-3 w-3 animate-spin rounded-full border-2 border-accent border-t-transparent"
      />
    </span>
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    disabled?: boolean
    loading?: boolean
    size?: 'sm' | 'md'
    title?: string
  }>(),
  {
    disabled: false,
    loading: false,
    size: 'md',
    title: '',
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', value: boolean): void
  (e: 'change', value: boolean): void
}>()

const sizeClasses = computed(() => {
  if (props.size === 'sm') {
    return {
      track: 'h-5 w-9',
      thumb: 'h-4 w-4',
      translate: 'translate-x-4',
    }
  }
  return {
    track: 'h-6 w-11',
    thumb: 'h-5 w-5',
    translate: 'translate-x-5',
  }
})

function toggle() {
  if (props.disabled || props.loading) return
  const next = !props.modelValue
  emit('update:modelValue', next)
  emit('change', next)
}
</script>
