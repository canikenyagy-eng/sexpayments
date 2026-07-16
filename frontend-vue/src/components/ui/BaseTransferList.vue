<template>
  <div class="space-y-2">
    <div v-if="label" class="flex items-end justify-between gap-2">
      <label class="block text-sm font-semibold text-text-secondary">{{ label }}</label>
      <span v-if="disabledHint && disabled" class="text-xs italic text-text-muted">
        {{ disabledHint }}
      </span>
    </div>

    <div class="grid grid-cols-1 gap-3 md:grid-cols-2" :class="disabled ? 'opacity-50 pointer-events-none' : ''">
      <!-- Available -->
      <div class="flex flex-col rounded-xl border border-border bg-bg-surface p-3">
        <div class="mb-2 flex items-center justify-between text-xs font-bold uppercase tracking-widest text-text-muted">
          <span>{{ availableLabel }}</span>
          <span class="rounded-full bg-bg-hover px-2 py-0.5 text-[10px]">{{ availableFiltered.length }}</span>
        </div>
        <input
          v-model="availableSearch"
          type="text"
          :placeholder="searchPlaceholder"
          class="input-field mb-2 text-sm"
          :disabled="disabled"
        />
        <ul class="max-h-64 min-h-[160px] flex-1 space-y-0.5 overflow-y-auto pr-1">
          <li
            v-for="opt in availableFiltered"
            :key="String(opt.value)"
            class="cursor-pointer rounded-lg px-2 py-1.5 text-sm text-text-secondary transition hover:bg-bg-hover hover:text-text-main"
            @click="add(opt.value)"
          >
            <div class="font-medium text-text-main">{{ opt.label }}</div>
            <div v-if="opt.sublabel" class="text-xs text-text-muted">{{ opt.sublabel }}</div>
          </li>
          <li
            v-if="!availableFiltered.length"
            class="px-2 py-3 text-center text-xs text-text-muted"
          >
            {{ availableSearch ? 'Ничего не найдено' : 'Все элементы выбраны' }}
          </li>
        </ul>
      </div>

      <!-- Selected -->
      <div class="flex flex-col rounded-xl border border-border bg-bg-surface p-3">
        <div class="mb-2 flex items-center justify-between text-xs font-bold uppercase tracking-widest text-text-muted">
          <span>{{ selectedLabel }}</span>
          <span class="rounded-full bg-accent/15 text-accent px-2 py-0.5 text-[10px]">{{ selectedItems.length }}</span>
        </div>
        <input
          v-model="selectedSearch"
          type="text"
          :placeholder="searchPlaceholder"
          class="input-field mb-2 text-sm"
          :disabled="disabled"
        />
        <ul class="max-h-64 min-h-[160px] flex-1 space-y-0.5 overflow-y-auto pr-1">
          <li
            v-for="opt in selectedFiltered"
            :key="String(opt.value)"
            class="cursor-pointer rounded-lg bg-accent/10 px-2 py-1.5 text-sm text-text-main transition hover:bg-accent/20"
            @click="remove(opt.value)"
          >
            <div class="flex items-center justify-between gap-2">
              <div class="min-w-0">
                <div class="truncate font-medium">{{ opt.label }}</div>
                <div v-if="opt.sublabel" class="truncate text-xs text-text-muted">{{ opt.sublabel }}</div>
              </div>
              <X class="h-4 w-4 shrink-0 text-text-muted" />
            </div>
          </li>
          <li
            v-if="!selectedFiltered.length"
            class="px-2 py-3 text-center text-xs text-text-muted"
          >
            {{ selectedSearch ? 'Ничего не найдено' : 'Ничего не выбрано' }}
          </li>
        </ul>
        <button
          v-if="selectedItems.length && !disabled"
          type="button"
          class="mt-2 self-end text-xs font-bold text-text-muted transition hover:text-status-danger"
          @click="clearAll"
        >
          Очистить
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { X } from 'lucide-vue-next'

export interface TransferOption {
  value: number | string
  label: string
  sublabel?: string
}

const props = withDefaults(defineProps<{
  modelValue: (number | string)[]
  options: TransferOption[]
  label?: string
  availableLabel?: string
  selectedLabel?: string
  searchPlaceholder?: string
  disabled?: boolean
  disabledHint?: string
}>(), {
  availableLabel: 'Доступные',
  selectedLabel: 'Выбранные',
  searchPlaceholder: 'Поиск…',
  disabled: false,
})

const emit = defineEmits<{ 'update:modelValue': [value: (number | string)[]] }>()

const availableSearch = ref('')
const selectedSearch = ref('')

const selectedSet = computed(() => new Set(props.modelValue.map(v => String(v))))

const selectedItems = computed(() =>
  props.options.filter(o => selectedSet.value.has(String(o.value))),
)

const availableItems = computed(() =>
  props.options.filter(o => !selectedSet.value.has(String(o.value))),
)

function applySearch(items: TransferOption[], term: string) {
  const t = term.trim().toLowerCase()
  if (!t) return items
  return items.filter(o =>
    o.label.toLowerCase().includes(t) ||
    (o.sublabel ? o.sublabel.toLowerCase().includes(t) : false) ||
    String(o.value).toLowerCase().includes(t),
  )
}

const availableFiltered = computed(() => applySearch(availableItems.value, availableSearch.value))
const selectedFiltered = computed(() => applySearch(selectedItems.value, selectedSearch.value))

function add(value: number | string) {
  if (props.disabled) return
  if (selectedSet.value.has(String(value))) return
  emit('update:modelValue', [...props.modelValue, value])
}

function remove(value: number | string) {
  if (props.disabled) return
  emit('update:modelValue', props.modelValue.filter(v => String(v) !== String(value)))
}

function clearAll() {
  if (props.disabled) return
  emit('update:modelValue', [])
}
</script>
