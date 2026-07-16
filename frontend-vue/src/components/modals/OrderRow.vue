<template>
  <!-- Key/value row with a dotted divider on TOP, dropped on the first row — so
       separators appear only BETWEEN rows (a lone row, or a row before a non-row
       block, shows no border). The value is inline-flex so an icon/logo value
       centres cleanly instead of sitting on the text baseline. -->
  <div class="flex items-center justify-between border-t border-dashed border-border-divider py-2 text-sm first:border-t-0">
    <span class="text-text-muted">{{ label }}</span>
    <span class="inline-flex items-center font-medium text-text-main"><slot /></span>
  </div>
</template>

<script setup lang="ts">
defineProps<{ label: string }>()
</script>
