<template>
  <div class="mb-6 border-b border-accent/10 pb-5">
    <div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <p class="sp-kicker mb-2">Рабочая область</p>
        <h1 class="text-[clamp(1.65rem,3vw,2.75rem)] font-black leading-none text-text-main">{{ title }}</h1>
      </div>
      <div v-if="$slots.actions" class="flex items-center gap-2">
        <slot name="actions" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  title: string
}>()
</script>
