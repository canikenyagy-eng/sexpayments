<template>
  <!-- Stat cell with a dashed border, used in the 2-col grids. -->
  <div class="rounded-lg border border-dashed border-border-divider px-3 py-2">
    <div class="text-[11px] text-text-muted">{{ label }}</div>
    <div class="text-base font-bold text-text-main"><slot /></div>
  </div>
</template>

<script setup lang="ts">
defineProps<{ label: string }>()
</script>
