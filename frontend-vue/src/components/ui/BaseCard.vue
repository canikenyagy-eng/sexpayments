<template>
  <div class="card">
    <div v-if="title || $slots.header" class="mb-4 flex items-center justify-between">
      <h3 v-if="title" class="text-base font-bold text-text-main">{{ title }}</h3>
      <slot name="header" />
    </div>
    <slot />
  </div>
</template>

<script setup lang="ts">
defineProps<{
  title?: string
}>()
</script>
