<template>
  <div class="space-y-4">
    <div v-if="!schema.length" class="text-sm text-text-muted italic">
      У этого адаптера нет настраиваемых полей.
    </div>

    <div v-for="field in schema" :key="field.key" class="space-y-1">
      <label class="block text-sm font-bold text-text-secondary">
        {{ field.label }}
        <span v-if="field.required" class="text-status-danger">*</span>
      </label>

      <!-- string / url -->
      <BaseInput
        v-if="field.type === 'string' || field.type === 'url'"
        :model-value="String(modelValue[field.key] ?? '')"
        :type="field.secret ? 'password' : 'text'"
        :placeholder="field.placeholder"
        @update:model-value="(v) => set(field.key, v)"
      />

      <!-- textarea -->
      <textarea
        v-else-if="field.type === 'textarea'"
        :value="String(modelValue[field.key] ?? '')"
        rows="4"
        class="w-full rounded-xl border border-border bg-bg-default p-3 text-sm"
        :placeholder="field.placeholder"
        @input="(e) => set(field.key, (e.target as HTMLTextAreaElement).value)"
      />

      <!-- number -->
      <BaseInput
        v-else-if="field.type === 'number'"
        type="number"
        :model-value="numberValue(field)"
        :placeholder="field.placeholder"
        :min="field.min ?? undefined"
        :max="field.max ?? undefined"
        @update:model-value="(v) => set(field.key, v === '' || v === null ? null : Number(v))"
      />

      <!-- boolean -->
      <label
        v-else-if="field.type === 'boolean'"
        class="flex items-center gap-2 text-sm text-text-secondary"
      >
        <input
          type="checkbox"
          class="h-4 w-4 accent-accent"
          :checked="!!modelValue[field.key]"
          @change="(e) => set(field.key, (e.target as HTMLInputElement).checked)"
        />
        Включено
      </label>

      <!-- select -->
      <BaseSelect
        v-else-if="field.type === 'select'"
        :model-value="String(modelValue[field.key] ?? '')"
        :options="(field.options ?? []).map((o) => ({ value: o.value, label: o.label }))"
        @update:model-value="(v) => set(field.key, v)"
      />

      <!-- multi_select -->
      <div v-else-if="field.type === 'multi_select'" class="flex flex-wrap gap-2">
        <label
          v-for="option in field.options ?? []"
          :key="option.value"
          class="inline-flex cursor-pointer items-center gap-2 rounded-xl border border-border bg-bg-default px-3 py-2 text-sm hover:border-accent"
          :class="{ '!border-accent text-accent': isInList(field.key, option.value) }"
        >
          <input
            type="checkbox"
            class="h-3.5 w-3.5 accent-accent"
            :checked="isInList(field.key, option.value)"
            @change="toggleListValue(field.key, option.value)"
          />
          {{ option.label }}
        </label>
      </div>

      <!-- kv_map -->
      <div v-else-if="field.type === 'kv_map'" class="space-y-2">
        <div
          v-for="(entry, idx) in kvState[field.key] ?? []"
          :key="`${field.key}-${idx}`"
          class="flex items-center gap-2"
        >
          <BaseInput
            class="flex-1"
            :model-value="entry.key"
            placeholder="ключ"
            @update:model-value="(v) => updateKvKey(field.key, idx, String(v))"
          />
          <span class="text-text-muted">→</span>
          <BaseSelect
            v-if="field.value_type === 'select'"
            class="flex-1"
            :model-value="entry.value"
            :options="(field.options ?? []).map((o) => ({ value: o.value, label: o.label }))"
            @update:model-value="(v) => updateKvValue(field.key, idx, String(v))"
          />
          <BaseInput
            v-else
            class="flex-1"
            :model-value="entry.value"
            placeholder="значение"
            @update:model-value="(v) => updateKvValue(field.key, idx, String(v))"
          />
          <button
            type="button"
            class="rounded-lg border border-border px-2 py-1 text-status-danger hover:bg-status-danger/10"
            @click="removeKv(field.key, idx)"
          >
            ×
          </button>
        </div>
        <button
          type="button"
          class="rounded-lg border border-dashed border-border px-3 py-1.5 text-sm text-text-muted hover:border-accent hover:text-accent"
          @click="addKv(field.key)"
        >
          + Добавить
        </button>
      </div>

      <p v-if="field.description" class="text-xs text-text-muted">
        {{ field.description }}
      </p>
    </div>
  </div>
</template>

<script setup lang="ts">
import { reactive, watch } from 'vue'
import type { AdapterFieldSpec } from '@/types'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'

interface KvEntry {
  key: string
  value: string
}

const props = defineProps<{
  schema: AdapterFieldSpec[]
  modelValue: Record<string, unknown>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: Record<string, unknown>): void
}>()

// Per kv_map field we keep an ordered list of (key, value) entries so the user
// can have empty rows while typing, and order is stable across renders. The
// outbound value is always serialized as a plain object via flushKv().
const kvState = reactive<Record<string, KvEntry[]>>({})

function rebuildKvState() {
  for (const field of props.schema) {
    if (field.type !== 'kv_map') continue
    const raw = props.modelValue[field.key]
    if (!raw || typeof raw !== 'object') {
      if (!kvState[field.key]) kvState[field.key] = []
      continue
    }
    // Only rebuild from props when our local state and props diverge — this
    // preserves blank rows the user just added.
    const fromProps: KvEntry[] = Object.entries(raw as Record<string, unknown>).map(
      ([k, v]) => ({ key: k, value: String(v ?? '') })
    )
    const current = kvState[field.key] ?? []
    const sameLength = current.length === fromProps.length
    const sameContent =
      sameLength &&
      current.every(
        (e, i) => e.key === fromProps[i].key && e.value === fromProps[i].value,
      )
    if (!sameContent) kvState[field.key] = fromProps
  }
}

watch(() => props.modelValue, rebuildKvState, { immediate: true, deep: true })
watch(() => props.schema, rebuildKvState)

function set(key: string, value: unknown) {
  emit('update:modelValue', { ...props.modelValue, [key]: value })
}

function isInList(key: string, value: string): boolean {
  const list = (props.modelValue[key] as string[] | undefined) ?? []
  return Array.isArray(list) && list.includes(value)
}

function toggleListValue(key: string, value: string) {
  const current = (props.modelValue[key] as string[] | undefined) ?? []
  const list = Array.isArray(current) ? current : []
  const next = list.includes(value)
    ? list.filter((x) => x !== value)
    : [...list, value]
  set(key, next)
}

function numberValue(field: AdapterFieldSpec): number | string {
  const raw = props.modelValue[field.key]
  if (raw === undefined || raw === null || raw === '') {
    return field.default == null ? '' : Number(field.default)
  }
  return Number(raw)
}

function flushKv(field: string) {
  const entries = kvState[field] ?? []
  const obj: Record<string, string> = {}
  for (const e of entries) {
    if (e.key.trim()) obj[e.key] = e.value
  }
  set(field, obj)
}

function updateKvKey(field: string, idx: number, newKey: string) {
  if (!kvState[field]) kvState[field] = []
  kvState[field][idx] = { ...kvState[field][idx], key: newKey }
  flushKv(field)
}

function updateKvValue(field: string, idx: number, newValue: string) {
  if (!kvState[field]) kvState[field] = []
  kvState[field][idx] = { ...kvState[field][idx], value: newValue }
  flushKv(field)
}

function addKv(field: string) {
  if (!kvState[field]) kvState[field] = []
  kvState[field].push({ key: '', value: '' })
  // No flush — empty rows aren't serialized yet, but the UI keeps the row
  // visible until the user types a key.
}

function removeKv(field: string, idx: number) {
  if (!kvState[field]) return
  kvState[field].splice(idx, 1)
  flushKv(field)
}
</script>
