<template>
  <div class="space-y-1.5">
    <label v-if="label" class="block text-sm font-semibold text-text-secondary">
      {{ label }}
    </label>
    <button
      type="button"
      class="input-field flex w-full items-center justify-between text-left disabled:opacity-50"
      :class="{ 'text-text-muted': !selected }"
      :disabled="disabled"
      @click="open = true"
    >
      <span class="flex min-w-0 items-center gap-2">
        <PaymentOptionLogo
          :src="selected?.logo_url ?? null"
          :alt="selected?.name ?? ''"
          :size="20"
        />
        <span class="truncate text-sm" :class="{ 'text-text-main font-medium': selected }">
          {{ selected ? selected.name : (placeholder || 'Выберите банк') }}
        </span>
      </span>
      <ChevronDown class="h-4 w-4 shrink-0 text-text-muted" />
    </button>
    <p v-if="error" class="text-xs text-status-danger">{{ error }}</p>

    <BaseModal v-model="open" title="Выберите банк" size="sm">
      <div class="space-y-3">
        <div class="relative">
          <Search class="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
          <input
            ref="searchInput"
            v-model="search"
            type="text"
            placeholder="Поиск по названию"
            class="input-field pl-10"
            @keydown.esc="open = false"
          />
        </div>

        <div class="max-h-[60vh] overflow-y-auto -mx-1 px-1">
          <ul v-if="filtered.length || (allLabel && !search)" class="space-y-1">
            <!-- Optional «show all / clear» row — only when a search isn't active. -->
            <li v-if="allLabel && !search">
              <button
                type="button"
                class="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition hover:bg-bg-hover"
                :class="{ 'bg-accent/10 ring-1 ring-accent/40': modelValue == null }"
                @click="selectAll"
              >
                <PaymentOptionLogo :src="null" :alt="allLabel" :size="32" />
                <span class="truncate text-sm font-semibold text-text-main">{{ allLabel }}</span>
                <Check v-if="modelValue == null" class="ml-auto h-4 w-4 shrink-0 text-accent" />
              </button>
            </li>
            <li v-for="opt in filtered" :key="opt.id">
              <button
                type="button"
                class="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-left transition hover:bg-bg-hover"
                :class="{ 'bg-accent/10 ring-1 ring-accent/40': opt.id === modelValue }"
                @click="select(opt)"
              >
                <PaymentOptionLogo
                  :src="opt.logo_url ?? null"
                  :alt="opt.name"
                  :size="32"
                />
                <span class="flex min-w-0 flex-1 flex-col">
                  <span class="truncate text-sm font-semibold text-text-main">{{ opt.name }}</span>
                  <span class="truncate text-xs text-text-muted">
                    {{ opt.currency }} · {{ opt.supported_methods.join(', ').toUpperCase() }}
                  </span>
                </span>
                <Check v-if="opt.id === modelValue" class="h-4 w-4 shrink-0 text-accent" />
              </button>
            </li>
          </ul>
          <p v-else class="py-8 text-center text-sm text-text-muted">
            Ничего не найдено
          </p>
        </div>
      </div>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { ChevronDown, Check, Search } from 'lucide-vue-next'
import BaseModal from './BaseModal.vue'
import PaymentOptionLogo from './PaymentOptionLogo.vue'
import type { PaymentOption } from '@/types'

const props = withDefaults(defineProps<{
  modelValue?: number | null
  options: PaymentOption[]
  label?: string
  placeholder?: string
  disabled?: boolean
  error?: string
  // When set, a top «show all» row appears that clears the selection (emits
  // null) — used to turn the picker into a filter with an «all banks» default.
  allLabel?: string
}>(), {
  modelValue: null,
})

const emit = defineEmits<{
  'update:modelValue': [value: number | null]
}>()

const open = ref(false)
const search = ref('')
const searchInput = ref<HTMLInputElement | null>(null)

const selected = computed(
  () => props.options.find(o => o.id === props.modelValue) ?? null,
)

const filtered = computed(() => {
  const q = search.value.trim().toLowerCase()
  if (!q) return props.options
  return props.options.filter(o =>
    o.name.toLowerCase().includes(q) || o.code.toLowerCase().includes(q),
  )
})

function select(opt: PaymentOption) {
  emit('update:modelValue', opt.id)
  open.value = false
}

function selectAll() {
  emit('update:modelValue', null)
  open.value = false
}

watch(open, async (val) => {
  if (val) {
    search.value = ''
    await nextTick()
    searchInput.value?.focus()
  }
})
</script>
