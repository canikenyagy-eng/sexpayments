<template>
  <div class="grid grid-cols-1 gap-2">
    <label
      v-for="p in providers"
      :key="p.id"
      class="flex cursor-pointer items-center justify-between gap-3 rounded-xl border border-border bg-bg-default p-3 transition hover:border-accent"
      :class="{
        '!border-accent': selected === p.id,
        'cursor-not-allowed opacity-50': disabled,
      }"
    >
      <div class="flex items-center gap-3">
        <input
          type="radio"
          class="accent-accent"
          :value="p.id"
          :checked="selected === p.id"
          :disabled="disabled"
          @change="selected = p.id"
        />
        <span class="font-bold text-text-main">{{ p.name }}</span>
      </div>
      <span class="shrink-0 font-bold text-accent">{{ formatPrice(p.price_usdt) }} USDT</span>
    </label>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import type { TraderReceiptProvider } from '@/types'

const props = withDefaults(
  defineProps<{
    modelValue: number | null
    providers: TraderReceiptProvider[]
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{ (e: 'update:modelValue', v: number | null): void }>()

const selected = computed<number | null>({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

function formatPrice(v: number): string {
  return Number(v ?? 0).toFixed(2)
}
</script>
