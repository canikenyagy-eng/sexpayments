<template>
  <div class="space-y-2">
    <label v-if="label" :for="id" class="block text-[11px] font-black uppercase tracking-[0.14em] text-text-muted">
      {{ label }}
    </label>
    <select
      :id="id"
      :value="modelValue"
      :disabled="disabled"
      class="input-field appearance-none bg-[url('data:image/svg+xml;charset=utf-8,%3Csvg%20xmlns%3D%22http%3A//www.w3.org/2000/svg%22%20width%3D%2212%22%20height%3D%2212%22%20viewBox%3D%220%200%2012%2012%22%3E%3Cpath%20fill%3D%22%238f8579%22%20d%3D%22M6%208L1%203h10z%22/%3E%3C/svg%3E')] bg-[length:12px] bg-[right_14px_center] bg-no-repeat pr-10 disabled:opacity-50"
      @change="$emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
    >
      <option v-if="placeholder" value="" disabled>{{ placeholder }}</option>
      <option
        v-for="opt in options"
        :key="opt.value"
        :value="opt.value"
      >
        {{ opt.label }}
      </option>
    </select>
  </div>
</template>

<script setup lang="ts">
export interface SelectOption {
  value: string | number
  label: string
}

withDefaults(defineProps<{
  modelValue?: string | number | null
  label?: string
  options: SelectOption[]
  placeholder?: string
  disabled?: boolean
  id?: string
}>(), {
  modelValue: '',
})

defineEmits<{ 'update:modelValue': [value: string] }>()
</script>
