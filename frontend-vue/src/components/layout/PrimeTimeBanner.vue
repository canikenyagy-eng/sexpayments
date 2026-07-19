<template>
  <div
    v-if="show"
    class="flex items-center justify-start gap-1.5 whitespace-nowrap rounded-lg border border-accent/20 bg-bg-main/35 px-2.5 py-1.5 text-[11px] font-black text-accent"
  >
    <Zap class="h-3.5 w-3.5 shrink-0" />
    <span>Праймтайм +{{ pointsLabel }}%</span>
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
