<template>
  <div class="card relative overflow-hidden">
    <div class="pointer-events-none absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/35 to-transparent" />
    <div v-if="title || $slots.header" class="relative mb-5 flex items-center justify-between gap-4">
      <h3 v-if="title" class="text-[15px] font-black uppercase tracking-[0.12em] text-text-main">{{ title }}</h3>
      <slot name="header" />
    </div>
    <div class="relative">
      <slot />
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  title?: string
}>()
</script>
