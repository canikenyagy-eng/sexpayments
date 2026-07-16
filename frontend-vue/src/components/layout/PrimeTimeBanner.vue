<template>
  <div
    v-if="show"
    class="flex items-center justify-center gap-1.5 whitespace-nowrap rounded-xl border border-accent/30 bg-accent/15 px-3 py-2 text-xs font-bold text-accent"
  >
    <Zap class="h-3.5 w-3.5 shrink-0" />
    <span>Primetime +{{ pointsLabel }}%</span>
    <span v-if="remainingLabel" class="font-mono font-semibold opacity-80">{{ remainingLabel }}</span>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { Zap } from 'lucide-vue-next'
import { usePrimeTime } from '@/composables/usePrimeTime'
import { useAuthStore } from '@/stores/auth'

const { active, points, remainingLabel, start } = usePrimeTime()
const authStore = useAuthStore()

onMounted(start)

// Only traders and admins see the Prime-Time strip.
const show = computed(
  () => active.value && (authStore.userRole === 'admin' || authStore.userRole === 'trader'),
)

// No trailing zeros: 1, 1.5, 2.25.
const pointsLabel = computed(() => Number(points.value).toString())
</script>
