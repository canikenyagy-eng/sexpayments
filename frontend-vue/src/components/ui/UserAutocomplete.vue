<template>
  <div class="space-y-1.5">
    <label v-if="label" class="block text-sm font-semibold text-text-secondary">
      {{ label }}
    </label>
    <div ref="rootRef" class="relative">
      <input
        :value="query"
        :placeholder="placeholder"
        :disabled="disabled"
        class="input-field disabled:opacity-50"
        autocomplete="off"
        @input="onInput(($event.target as HTMLInputElement).value)"
        @focus="onFocus"
        @keydown.down.prevent="move(1)"
        @keydown.up.prevent="move(-1)"
        @keydown.enter.prevent="selectHighlighted"
        @keydown.esc="open = false"
      />
      <div
        v-if="open && (suggestions.length > 0 || searching)"
        class="absolute left-0 right-0 top-[calc(100%+4px)] z-30 max-h-64 overflow-y-auto rounded-xl border border-border bg-bg-surface shadow-lg"
      >
        <div v-if="searching" class="px-3 py-2 text-xs text-text-muted">Поиск...</div>
        <button
          v-for="(item, idx) in suggestions"
          :key="item.id"
          type="button"
          class="flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-sm transition"
          :class="idx === highlighted ? 'bg-accent/15 text-text-main' : 'text-text-secondary hover:bg-bg-hover'"
          @mouseenter="highlighted = idx"
          @click="pick(item)"
        >
          <span class="flex items-center gap-2 font-semibold">
            {{ item.username }}
            <RoleBadge :role="item.role" />
          </span>
          <span class="text-xs text-text-muted">#{{ item.id }}</span>
        </button>
      </div>
    </div>
    <p v-if="hint" class="text-xs text-text-muted">{{ hint }}</p>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue'
import { usersService } from '@/api/services/users.service'
import RoleBadge from '@/components/ui/RoleBadge.vue'
import type { User, UserRole } from '@/types'

const props = withDefaults(defineProps<{
  modelValue?: number | null
  role?: UserRole
  label?: string
  placeholder?: string
  disabled?: boolean
  hint?: string
}>(), {
  placeholder: 'ID или логин',
})

const emit = defineEmits<{
  'update:modelValue': [value: number | null]
  'select': [user: User]
}>()

const rootRef = ref<HTMLElement | null>(null)
const query = ref('')
const suggestions = ref<User[]>([])
const searching = ref(false)
const open = ref(false)
const highlighted = ref(0)

let debounceTimer: ReturnType<typeof setTimeout> | null = null
let reqId = 0

async function search(term: string) {
  const myReq = ++reqId
  searching.value = true
  try {
    const params: { search: string; role?: UserRole; limit: number } = {
      search: term,
      limit: 10,
    }
    if (props.role) params.role = props.role
    const { data } = await usersService.list(params)
    if (myReq !== reqId) return
    suggestions.value = data
    highlighted.value = 0
  } catch {
    if (myReq === reqId) suggestions.value = []
  } finally {
    if (myReq === reqId) searching.value = false
  }
}

function onInput(value: string) {
  query.value = value
  emit('update:modelValue', null)
  open.value = true
  if (debounceTimer) clearTimeout(debounceTimer)
  if (!value.trim()) {
    suggestions.value = []
    return
  }
  debounceTimer = setTimeout(() => {
    void search(value.trim())
  }, 250)
}

function onFocus() {
  if (query.value.trim()) open.value = true
}

function pick(user: User) {
  query.value = `${user.username} (#${user.id})`
  emit('update:modelValue', user.id)
  emit('select', user)
  open.value = false
}

function selectHighlighted() {
  const item = suggestions.value[highlighted.value]
  if (item) pick(item)
}

function move(delta: number) {
  if (!suggestions.value.length) return
  const next = highlighted.value + delta
  highlighted.value = (next + suggestions.value.length) % suggestions.value.length
  open.value = true
}

function handleClickOutside(event: MouseEvent) {
  if (!rootRef.value) return
  if (!rootRef.value.contains(event.target as Node)) {
    open.value = false
  }
}

if (typeof document !== 'undefined') {
  document.addEventListener('mousedown', handleClickOutside)
}

onBeforeUnmount(() => {
  if (typeof document !== 'undefined') {
    document.removeEventListener('mousedown', handleClickOutside)
  }
  if (debounceTimer) clearTimeout(debounceTimer)
})

watch(() => props.modelValue, (val) => {
  if (val == null) {
    if (!query.value) return
  }
})

defineExpose({
  reset() {
    query.value = ''
    suggestions.value = []
    open.value = false
    emit('update:modelValue', null)
  },
  setInitial(user: { id: number; username: string }) {
    query.value = `${user.username} (#${user.id})`
    emit('update:modelValue', user.id)
  },
})
</script>
