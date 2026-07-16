<template>
  <div class="flex flex-col items-center justify-center py-16">
    <span class="h-8 w-8 animate-spin rounded-full border-3 border-accent border-t-transparent" />
    <p class="mt-4 text-sm text-text-muted">{{ text }}</p>
  </div>
</template>

<script setup lang="ts">
withDefaults(defineProps<{
  text?: string
}>(), {
  text: 'Загрузка...',
})
</script>
