<template>
  <span
    v-if="role"
    :class="[
      'inline-flex items-center gap-1 rounded-lg font-bold',
      sizeClass,
      colorClass,
    ]"
  >
    <component :is="icon" :class="iconSizeClass" />
    <span>{{ label }}</span>
  </span>
  <span v-else class="text-text-muted">—</span>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { Headset, ShieldCheck, Store, TrendingUp, Users } from 'lucide-vue-next'
import type { UserRole } from '@/types'

const props = withDefaults(
  defineProps<{
    role?: UserRole | string | null
    size?: 'sm' | 'md'
  }>(),
  {
    size: 'sm',
  },
)

const sizeClass = computed(() =>
  props.size === 'md' ? 'px-2.5 py-1 text-xs' : 'px-2 py-0.5 text-[11px]',
)

const iconSizeClass = computed(() =>
  props.size === 'md' ? 'h-3.5 w-3.5 shrink-0' : 'h-3 w-3 shrink-0',
)

const config = computed(() => {
  switch (props.role) {
    case 'admin':
      return {
        label: 'Админ',
        icon: ShieldCheck,
        cls: 'bg-status-danger/15 text-status-danger',
      }
    case 'support':
      return {
        label: 'Саппорт',
        icon: Headset,
        cls: 'bg-accent/15 text-accent',
      }
    case 'merchant':
      return {
        label: 'Мерчант',
        icon: Store,
        cls: 'bg-status-info/15 text-status-info',
      }
    case 'trader':
      return {
        label: 'Трейдер',
        icon: TrendingUp,
        cls: 'bg-status-success/15 text-status-success',
      }
    case 'teamlead':
      return {
        label: 'Тимлид',
        icon: Users,
        cls: 'bg-accent/15 text-accent',
      }
    default:
      return {
        label: String(props.role || ''),
        icon: Users,
        cls: 'bg-bg-hover text-text-secondary',
      }
  }
})

const label = computed(() => config.value.label)
const icon = computed(() => config.value.icon)
const colorClass = computed(() => config.value.cls)
</script>
