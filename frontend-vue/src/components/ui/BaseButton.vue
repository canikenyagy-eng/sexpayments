<template>
  <button
    :type="type"
    :disabled="disabled || loading"
    :title="($attrs.title as string) || actionTitle"
    :class="[
      'inline-flex items-center justify-center gap-2 rounded-xl border font-black transition-all duration-200 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed disabled:hover:translate-y-0',
      sizeClasses,
      variantClasses,
      { 'hover:-translate-y-px active:translate-y-0': !disabled && !loading && variant !== 'icon' },
    ]"
  >
    <span v-if="loading" class="h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
    <component v-if="actionIcon && !loading" :is="actionIcon" class="h-4 w-4" />
    <slot />
  </button>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import {
  Pencil,
  Trash2,
  Check,
  X,
  Search,
  RefreshCw,
  Key,
  UserCircle
} from 'lucide-vue-next'

const props = withDefaults(defineProps<{
  variant?: 'gold' | 'dark' | 'danger' | 'ghost' | 'success' | 'icon'
  size?: 'sm' | 'md' | 'lg'
  type?: 'button' | 'submit' | 'reset'
  disabled?: boolean
  loading?: boolean
  action?: 'edit' | 'delete' | 'accept' | 'cancel' | 'view' | 'refresh' | 'key' | 'impersonate'
}>(), {
  variant: 'gold',
  size: 'md',
  type: 'button',
})

const actionConfig = computed(() => {
  switch (props.action) {
    case 'edit': return { icon: Pencil, title: 'Редактировать' }
    case 'delete': return { icon: Trash2, title: 'Удалить' }
    case 'accept': return { icon: Check, title: 'Одобрить' }
    case 'cancel': return { icon: X, title: 'Отклонить' }
    case 'view': return { icon: Search, title: 'Детали' }
    case 'refresh': return { icon: RefreshCw, title: 'Обновить' }
    case 'key': return { icon: Key, title: 'Сбросить ключи' }
    case 'impersonate': return { icon: UserCircle, title: 'Войти как' }
    default: return null
  }
})

const actionIcon = computed(() => actionConfig.value?.icon)
const actionTitle = computed(() => actionConfig.value?.title)

const sizeClasses = computed(() =>
  // Вариант icon без внутренних отступов, размер задает вызывающий компонент.
  props.variant === 'icon' ? '' : {
    sm: 'px-3 py-2 text-xs',
    md: 'px-4 py-2.5 text-sm',
    lg: 'px-6 py-3.5 text-base',
  }[props.size])

const variantClasses = computed(() => ({
  gold: 'border-accent/25 bg-[linear-gradient(135deg,rgba(214,163,143,0.18),transparent_38%),linear-gradient(180deg,#9E1B43_0%,#74112E_100%)] text-text-main shadow-[0_18px_42px_rgba(139,21,56,0.28),inset_0_1px_0_rgba(245,245,245,0.08)] hover:border-accent/45',
  dark: 'border-accent/15 bg-bg-surface/70 text-text-main shadow-[inset_0_1px_0_rgba(245,245,245,0.04)] hover:border-accent/30 hover:bg-bg-hover/70',
  danger: 'border-status-danger/25 bg-status-danger/10 text-status-danger hover:bg-status-danger/18',
  ghost: 'border-transparent text-text-secondary hover:border-accent/15 hover:bg-bg-hover/45 hover:text-text-main',
  success: 'border-status-success/25 bg-status-success/10 text-status-success hover:bg-status-success/18',
  icon: 'border-transparent hover:opacity-70',
}[props.variant]))
</script>
