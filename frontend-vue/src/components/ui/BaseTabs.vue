<template>
  <div>
    <!-- Tab strip in a TabScroller: when the tabs don't fit, left/right chevrons
         appear and the strip scrolls (native scrollbar hidden via
         ``no-scrollbar``). The bottom border lives on the scroller so the
         underline runs the full width, under the chevrons too. -->
    <TabScroller class="mb-4 border-b border-border">
      <div class="flex items-center gap-1">
        <button
          v-for="tab in tabs"
          :key="tab.key"
          type="button"
          class="relative -mb-px inline-flex shrink-0 items-center gap-2 px-4 py-2 text-sm font-bold transition-colors"
          :class="modelValue === tab.key
            ? 'text-accent border-b-2 border-accent'
            : 'text-text-muted hover:text-text-main border-b-2 border-transparent'"
          @click="select(tab.key)"
        >
          <span>{{ tab.label }}</span>
          <span
            v-if="typeof tab.count === 'number'"
            class="flex h-5 min-w-5 items-center justify-center rounded-full bg-bg-hover px-1.5 text-[10px] font-black"
            :class="modelValue === tab.key ? 'bg-accent text-bg-main' : 'text-text-muted'"
          >
            {{ tab.count }}
          </span>
        </button>
      </div>
    </TabScroller>

    <div>
      <slot :name="modelValue" />
    </div>
  </div>
</template>

<script setup lang="ts">
import TabScroller from './TabScroller.vue'

export interface Tab {
  key: string
  label: string
  count?: number
}

defineProps<{
  modelValue: string
  tabs: Tab[]
}>()

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

function select(key: string) {
  emit('update:modelValue', key)
}
</script>
