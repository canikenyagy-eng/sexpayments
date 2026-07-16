<template>
  <Teleport to="body">
    <div class="fixed right-4 top-4 z-[100] flex flex-col gap-2">
      <TransitionGroup name="toast">
        <div
          v-for="toast in toasts"
          :key="toast.id"
          :class="[
            'flex items-center gap-3 rounded-xl border px-4 py-3 text-sm font-bold shadow-prime backdrop-blur-md',
            colorMap[toast.type],
          ]"
        >
          <CheckCircle2 v-if="toast.type === 'success'" class="h-4 w-4" />
          <XCircle v-else-if="toast.type === 'error'" class="h-4 w-4" />
          <AlertTriangle v-else-if="toast.type === 'warning'" class="h-4 w-4" />
          <Info v-else class="h-4 w-4" />
          <span>{{ toast.message }}</span>
        </div>
      </TransitionGroup>
    </div>
  </Teleport>
</template>

<script setup lang="ts">
import { useToast } from '@/composables/useToast'
import { CheckCircle2, XCircle, AlertTriangle, Info } from 'lucide-vue-next'

const { toasts } = useToast()

const colorMap: Record<string, string> = {
  success: 'bg-status-success/15 border-status-success/30 text-status-success',
  error: 'bg-status-danger/15 border-status-danger/30 text-status-danger',
  warning: 'bg-status-warning/15 border-status-warning/30 text-status-warning',
  info: 'bg-accent/15 border-accent/30 text-accent',
}
</script>

<style scoped>
.toast-enter-active { transition: all 0.3s ease; }
.toast-leave-active { transition: all 0.2s ease; }
.toast-enter-from { opacity: 0; transform: translateX(40px); }
.toast-leave-to { opacity: 0; transform: translateX(40px); }
</style>
