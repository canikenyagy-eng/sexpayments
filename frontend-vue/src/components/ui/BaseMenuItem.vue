<template>
  <button
    type="button"
    :disabled="disabled"
    class="flex w-full items-center gap-2 px-4 py-2 text-left text-sm transition"
    :class="[
      disabled
        ? 'cursor-not-allowed opacity-50'
        : 'cursor-pointer hover:bg-bg-hover',
      dangerous ? 'text-status-danger' : 'text-text-main',
    ]"
    @click="onClick"
  >
    <component v-if="icon" :is="icon" class="h-4 w-4 shrink-0" />
    <span class="flex-1 truncate"><slot /></span>
  </button>
</template>

<script setup lang="ts">
import type { Component } from 'vue'

const props = defineProps<{
  icon?: Component
  dangerous?: boolean
  disabled?: boolean
}>()

const emit = defineEmits<{ click: [event: MouseEvent] }>()

function onClick(event: MouseEvent) {
  if (props.disabled) {
    event.stopPropagation()
    return
  }
  emit('click', event)
}
</script>
