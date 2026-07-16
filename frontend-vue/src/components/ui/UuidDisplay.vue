<template>
  <button
    v-if="value"
    type="button"
    class="group inline-flex items-center gap-1 rounded px-1 py-0.5 transition-colors hover:bg-bg-hover active:bg-bg-card"
    :title="title || 'Скопировать'"
    @click.stop="onCopy"
  >
    <span class="font-mono text-xs text-text-main transition-colors group-hover:text-accent">
      {{ display }}
    </span>
    <Copy
      v-if="showIcon"
      class="h-3 w-3 shrink-0 text-text-muted opacity-60 transition-opacity group-hover:opacity-100 group-hover:text-accent"
    />
  </button>
  <span v-else class="text-text-muted">—</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Copy } from 'lucide-vue-next'
import { copyToClipboard } from '@/utils/format'
import { useToast } from '@/composables/useToast'

type Variant = 'short' | 'full' | 'icon'

const props = withDefaults(
  defineProps<{
    value: string | null | undefined
    /**
     * Display mode:
     *  - short — first N chars (default 8)
     *  - full  — full string
     *  - icon  — full string + copy icon on the right
     */
    variant?: Variant
    /** Used only when variant === 'short' */
    length?: number
    /** Tooltip override */
    title?: string
    /** Legacy prop kept for backwards compatibility — same as `variant: 'short'` when true. */
    truncate?: boolean
    /** Legacy prop kept for backwards compatibility — turns on the copy icon. */
    showIcon?: boolean
    /**
     * Override the toast text shown on successful copy. Default
     * "UUID скопирован" is wrong when the component is repurposed for
     * external IDs / order references — pass e.g. `"External ID
     * скопирован"` or just `"Скопировано"` in those cases.
     */
    successMessage?: string
  }>(),
  {
    variant: 'short',
    length: 8,
    truncate: undefined,
    showIcon: undefined,
    successMessage: 'UUID скопирован',
  },
)

const toast = useToast()

const effectiveVariant = computed<Variant>(() => {
  // Legacy props take precedence so existing call sites keep working
  if (props.showIcon === true) return 'icon'
  if (props.truncate === false) return 'full'
  if (props.truncate === true) return 'short'
  return props.variant
})

const display = computed(() => {
  const v = props.value ?? ''
  return effectiveVariant.value === 'short' ? v.slice(0, props.length) : v
})

const showIcon = computed(() => effectiveVariant.value === 'icon')

async function onCopy() {
  if (!props.value) return
  try {
    await copyToClipboard(props.value)
    toast.success(props.successMessage)
  } catch {
    toast.error('Не удалось скопировать')
  }
}
</script>
