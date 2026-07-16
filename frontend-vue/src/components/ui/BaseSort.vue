<template>
  <div class="relative" ref="wrapperRef">
    <button
      type="button"
      class="inline-flex items-center gap-2 rounded-xl border border-border bg-bg-hover px-3 py-2 text-sm font-bold text-text-main transition-all hover:bg-bg-card"
      @click="toggleOpen"
    >
      <ArrowUpDown class="h-4 w-4 text-text-muted" />
      <span v-if="activeLabel" class="max-w-[140px] truncate">{{ activeLabel }}</span>
      <span v-else>Сортировка</span>
      <component
        v-if="modelValue.key"
        :is="modelValue.order === 'asc' ? ArrowUp : ArrowDown"
        class="h-3.5 w-3.5 text-accent"
      />
    </button>

    <Transition
      enter-active-class="transition duration-150 ease-out"
      enter-from-class="scale-95 opacity-0"
      enter-to-class="scale-100 opacity-100"
      leave-active-class="transition duration-100 ease-in"
      leave-from-class="scale-100 opacity-100"
      leave-to-class="scale-95 opacity-0"
    >
      <div
        v-if="open"
        class="absolute top-full z-50 mt-2 min-w-[200px] max-w-[calc(100vw-2rem)] rounded-2xl border border-border bg-bg-surface py-1 shadow-xl"
        :class="alignRight ? 'right-0' : 'left-0'"
      >
        <div class="px-3 pb-1 pt-2">
          <span class="text-[10px] font-bold uppercase tracking-widest text-text-muted">Сортировка</span>
        </div>

        <button
          v-for="opt in options"
          :key="opt.key"
          type="button"
          class="flex w-full items-center justify-between gap-3 px-3 py-2 text-sm transition hover:bg-bg-hover"
          :class="modelValue.key === opt.key ? 'text-accent font-bold' : 'text-text-secondary'"
          @click="toggle(opt)"
        >
          <span>{{ opt.label }}</span>
          <component
            v-if="modelValue.key === opt.key"
            :is="modelValue.order === 'asc' ? ArrowUp : ArrowDown"
            class="h-3.5 w-3.5"
          />
        </button>

        <div v-if="modelValue.key" class="border-t border-border px-3 py-1">
          <button
            type="button"
            class="flex w-full items-center gap-2 py-1.5 text-xs font-bold text-text-muted transition hover:text-status-danger"
            @click="reset"
          >
            <X class="h-3.5 w-3.5" />
            Сброс
          </button>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { ArrowUpDown, ArrowUp, ArrowDown, X } from 'lucide-vue-next'

export interface SortOption {
  key: string
  label: string
}

export interface SortValue {
  key: string
  order: 'asc' | 'desc'
}

const props = defineProps<{
  modelValue: SortValue
  options: SortOption[]
}>()

const emit = defineEmits<{
  'update:modelValue': [value: SortValue]
  change: [value: SortValue]
}>()

const open = ref(false)
const alignRight = ref(false)
const wrapperRef = ref<HTMLElement | null>(null)

const activeLabel = computed(() =>
  props.options.find(o => o.key === props.modelValue.key)?.label,
)

function updateAlignment() {
  const wrapper = wrapperRef.value
  if (!wrapper) return
  const rect = wrapper.getBoundingClientRect()
  const panelWidth = 240
  const margin = 16
  alignRight.value = rect.left + panelWidth + margin > window.innerWidth
}

async function toggleOpen() {
  if (open.value) {
    open.value = false
    return
  }
  updateAlignment()
  open.value = true
  await nextTick()
  updateAlignment()
}

function toggle(opt: SortOption) {
  let order: 'asc' | 'desc' = 'desc'
  if (props.modelValue.key === opt.key) {
    order = props.modelValue.order === 'desc' ? 'asc' : 'desc'
  }
  const val: SortValue = { key: opt.key, order }
  emit('update:modelValue', val)
  emit('change', val)
  open.value = false
}

function reset() {
  const val: SortValue = { key: '', order: 'desc' }
  emit('update:modelValue', val)
  emit('change', val)
  open.value = false
}

function onClickOutside(e: MouseEvent) {
  if (wrapperRef.value && !wrapperRef.value.contains(e.target as Node)) {
    open.value = false
  }
}

function onResize() {
  if (open.value) updateAlignment()
}

onMounted(() => {
  document.addEventListener('mousedown', onClickOutside)
  window.addEventListener('resize', onResize)
})
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onClickOutside)
  window.removeEventListener('resize', onResize)
})
</script>
