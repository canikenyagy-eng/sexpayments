<template>
  <div class="space-y-1.5">
    <label v-if="label" :for="id" class="block text-sm font-semibold text-text-secondary">
      {{ label }}
    </label>
    <div class="relative">
      <input
        :id="id"
        :type="inputType"
        :value="modelValue"
        :placeholder="placeholder"
        :disabled="disabled"
        :required="required"
        class="input-field disabled:opacity-50"
        :class="{ 'pr-12': type === 'password' }"
        @input="$emit('update:modelValue', ($event.target as HTMLInputElement).value)"
      />
      <button
        v-if="type === 'password'"
        type="button"
        class="absolute right-1 top-1/2 flex h-10 w-10 -translate-y-1/2 items-center justify-center rounded-lg text-text-muted transition hover:bg-bg-hover hover:text-text-main focus:outline-none"
        @click="togglePasswordVisibility"
        tabindex="-1"
      >
        <Eye v-if="!showPassword" class="h-4 w-4" />
        <EyeOff v-else class="h-4 w-4" />
      </button>
    </div>
    <p v-if="error" class="text-xs text-status-danger">{{ error }}</p>
  </div>
</template>

<script setup lang="ts">
import { ref, computed } from 'vue'
import { Eye, EyeOff } from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  modelValue?: string | number
  label?: string
  type?: string
  placeholder?: string
  disabled?: boolean
  required?: boolean
  error?: string
  id?: string
}>(), {
  type: 'text',
  modelValue: '',
})

defineEmits<{ 'update:modelValue': [value: string] }>()

const showPassword = ref(false)

const inputType = computed(() => {
  if (props.type === 'password') {
    return showPassword.value ? 'text' : 'password'
  }
  return props.type
})

function togglePasswordVisibility() {
  showPassword.value = !showPassword.value
}
</script>
