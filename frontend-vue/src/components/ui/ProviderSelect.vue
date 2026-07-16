<template>
  <div class="space-y-1.5">
    <label v-if="label" class="block text-sm font-semibold text-text-secondary">
      {{ label }}
    </label>
    <div ref="rootRef" class="relative">
      <input
        :value="open ? query : selectedLabel"
        :placeholder="placeholder"
        class="input-field"
        autocomplete="off"
        @focus="onFocus"
        @input="onInput(($event.target as HTMLInputElement).value)"
        @keydown.down.prevent="move(1)"
        @keydown.up.prevent="move(-1)"
        @keydown.enter.prevent="selectHighlighted"
        @keydown.esc="open = false"
      />
      <div
        v-if="open"
        class="absolute left-0 right-0 top-[calc(100%+4px)] z-30 max-h-64 overflow-y-auto rounded-xl border border-border bg-bg-surface shadow-lg"
      >
        <button
          type="button"
          class="flex w-full items-center px-3 py-2 text-left text-sm font-semibold transition"
          :class="!modelValue ? 'bg-accent/15 text-text-main' : 'text-text-secondary hover:bg-bg-hover'"
          @click="pickAll"
        >
          Все провайдеры
        </button>
        <div v-if="loading" class="px-3 py-2 text-xs text-text-muted">Загрузка…</div>
        <div v-else-if="!filtered.length" class="px-3 py-2 text-xs text-text-muted">Ничего не найдено</div>
        <button
          v-for="(p, idx) in filtered"
          :key="p.id"
          type="button"
          class="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm transition"
          :class="idx === highlighted ? 'bg-accent/15 text-text-main' : 'text-text-secondary hover:bg-bg-hover'"
          @mouseenter="highlighted = idx"
          @click="pick(p)"
        >
          <span class="flex items-center gap-2 font-semibold">
            {{ p.name }}
            <span v-if="!p.is_active" class="text-xs text-text-muted">(выкл.)</span>
          </span>
          <span class="font-mono text-xs text-text-muted">{{ p.code }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { cascadeService } from '@/api/services/cascade.service'
import type { CascadeProvider } from '@/types'

const props = withDefaults(defineProps<{
  modelValue?: string
  label?: string
  placeholder?: string
}>(), {
  modelValue: '',
  placeholder: 'Все провайдеры',
})

const emit = defineEmits<{ 'update:modelValue': [value: string] }>()

const rootRef = ref<HTMLElement | null>(null)
const providers = ref<CascadeProvider[]>([])
const loading = ref(false)
const query = ref('')
const open = ref(false)
const highlighted = ref(0)

// Closed-state label: the selected provider's name (falls back to its raw code
// until the list has loaded), or empty so the placeholder shows for "all".
const selectedLabel = computed(() => {
  if (!props.modelValue) return ''
  const p = providers.value.find(x => x.code === props.modelValue)
  return p ? p.name : props.modelValue
})

const filtered = computed(() => {
  const q = query.value.trim().toLowerCase()
  if (!q) return providers.value
  return providers.value.filter(
    p => p.name.toLowerCase().includes(q) || p.code.toLowerCase().includes(q),
  )
})

async function loadProviders() {
  loading.value = true
  try {
    const { data } = await cascadeService.listProviders({ limit: 200 })
    providers.value = data
  } catch {
    providers.value = []
  } finally {
    loading.value = false
  }
}

function onFocus(e: FocusEvent) {
  // Clear the query so the full list is shown; typing then filters it.
  query.value = ''
  highlighted.value = 0
  open.value = true
  ;(e.target as HTMLInputElement).select?.()
}

function onInput(value: string) {
  query.value = value
  highlighted.value = 0
  open.value = true
}

function pick(p: CascadeProvider) {
  emit('update:modelValue', p.code)
  query.value = ''
  open.value = false
}

function pickAll() {
  emit('update:modelValue', '')
  query.value = ''
  open.value = false
}

function selectHighlighted() {
  const p = filtered.value[highlighted.value]
  if (p) pick(p)
}

function move(delta: number) {
  open.value = true
  const n = filtered.value.length
  if (!n) return
  highlighted.value = (highlighted.value + delta + n) % n
}

function handleClickOutside(event: MouseEvent) {
  if (rootRef.value && !rootRef.value.contains(event.target as Node)) open.value = false
}

onMounted(() => {
  loadProviders()
  if (typeof document !== 'undefined') {
    document.addEventListener('mousedown', handleClickOutside)
  }
})

onBeforeUnmount(() => {
  if (typeof document !== 'undefined') {
    document.removeEventListener('mousedown', handleClickOutside)
  }
})
</script>
