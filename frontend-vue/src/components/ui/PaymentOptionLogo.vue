<template>
  <span
    class="shrink-0 inline-flex items-center justify-center overflow-hidden rounded-sm"
    :class="showFallback ? 'bg-bg-card text-text-muted' : ''"
    :style="{ width: sizePx + 'px', height: sizePx + 'px' }"
    :aria-label="alt"
  >
    <img
      v-if="!showFallback"
      :src="src!"
      :alt="alt"
      class="h-full w-full object-contain"
      loading="lazy"
      decoding="async"
      @error="onError"
    />
    <HelpCircle
      v-else
      class="opacity-70"
      :style="{ width: iconPx + 'px', height: iconPx + 'px' }"
      :aria-hidden="true"
    />
  </span>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { HelpCircle } from 'lucide-vue-next'

const props = withDefaults(
  defineProps<{
    src?: string | null
    alt?: string
    size?: number | string
  }>(),
  {
    src: null,
    alt: '',
    size: 20,
  },
)

const errored = ref(false)

watch(
  () => props.src,
  () => {
    errored.value = false
  },
)

const sizePx = computed(() =>
  typeof props.size === 'number' ? props.size : Number(props.size) || 20,
)

const iconPx = computed(() => Math.max(10, Math.round(sizePx.value * 0.6)))

const showFallback = computed(() => !props.src || errored.value)

function onError(): void {
  errored.value = true
}
</script>
