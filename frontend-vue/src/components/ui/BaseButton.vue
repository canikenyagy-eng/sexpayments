<template>
  <button
    :type="type"
    :disabled="disabled || loading"
    :title="($attrs.title as string) || actionTitle"
    :class="[
      'inline-flex items-center justify-center gap-2 rounded-xl font-bold transition-all duration-200 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed',
      sizeClasses,
      variantClasses,
      { 'hover:-translate-y-px': !disabled && !loading && variant !== 'icon' },
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
  // `icon` is padding-less — it's just the glyph, so size (padding) doesn't apply.
  props.variant === 'icon' ? '' : {
    sm: 'px-3 py-2 text-xs',
    md: 'px-4 py-2.5 text-sm',
    lg: 'px-6 py-3.5 text-base',
  }[props.size])

const variantClasses = computed(() => ({
  gold: 'bg-gold-gradient text-text-main shadow-prime',
  dark: 'bg-bg-hover text-text-main border border-border hover:bg-bg-card',
  danger: 'bg-status-danger/15 text-status-danger border border-status-danger/30 hover:bg-status-danger/25',
  ghost: 'text-text-secondary hover:text-text-main hover:bg-bg-hover',
  success: 'bg-status-success/15 text-status-success border border-status-success/30 hover:bg-status-success/25',
  // Bare icon — no bg / border / padding; caller sets the colour via `class`
  // (e.g. text-status-danger). Only dims on hover.
  icon: 'hover:opacity-70',
}[props.variant]))
</script>
