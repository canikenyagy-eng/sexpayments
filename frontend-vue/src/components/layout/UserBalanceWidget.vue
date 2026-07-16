<template>
  <div class="relative" ref="widgetRef">
    <!-- Trigger — sidebar (full-width card) vs topbar (compact pill) -->
    <button
      type="button"
      :class="[
        'flex items-center gap-3 transition-colors',
        'w-full rounded-xl px-3 py-2.5 text-left hover:bg-bg-hover',
        isOpen ? 'bg-bg-hover' : '',
      ]"
      @click="toggle"
    >
      <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/15">
        <Wallet class="h-4 w-4 text-accent" />
      </div>
      <div class="min-w-0 flex-1">
        <p class="truncate text-sm font-bold text-text-main leading-none">
          {{ formatAmount(workBalanceUsdt) }} USDT
        </p>
        <p class="text-xs text-text-muted leading-none mt-0.5">Рабочий баланс</p>
      </div>
      <ChevronDown
        class="h-4 w-4 shrink-0 text-text-muted transition-transform duration-200"
        :class="isOpen ? 'rotate-180' : ''"
      />
    </button>

    <!-- Dropdown — always opens downward in sidebar, downward in topbar -->
    <Transition name="dropdown">
      <div
        v-if="isOpen"
        class="absolute left-0 right-0 top-full z-50 mt-1 rounded-xl border border-border bg-bg-surface p-3 shadow-lg"
      >
        <div class="mb-3 space-y-2">
          <BaseButton variant="gold" class="w-full" @click="openTopup">Пополнить</BaseButton>
          <BaseButton
            variant="dark"
            class="w-full"
            :disabled="!canWithdraw"
            :title="canWithdraw ? '' : 'Нет доступного баланса для вывода'"
            @click="openWithdraw"
          >
            Вывести
          </BaseButton>
        </div>

        <div v-if="loading" class="py-4 text-center">
          <span class="inline-block h-5 w-5 animate-spin rounded-full border-2 border-accent border-t-transparent" />
        </div>
        <div v-else-if="balances.length === 0" class="py-2 text-center text-xs text-text-muted">
          Нет активных балансов
        </div>
        <div v-else class="space-y-1.5">
          <div
            v-for="b in balances"
            :key="b.id"
            class="flex items-center justify-between rounded-lg bg-bg-card px-3 py-2"
          >
            <div class="flex flex-col">
              <span class="text-xs font-bold text-text-main uppercase">{{ b.type }}</span>
              <span class="text-[10px] text-text-muted">{{ b.currency }}</span>
            </div>
            <span class="text-sm font-bold text-text-main tabular-nums">
              {{ formatAmount(b.amount) }}
            </span>
          </div>
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Wallet, ChevronDown } from 'lucide-vue-next'
import { onClickOutside } from '@vueuse/core'
import { financesService } from '@/api/services/finances.service'
import type { BalanceInfo } from '@/types'
import { formatAmount } from '@/utils/format'
import BaseButton from '@/components/ui/BaseButton.vue'
import { useToast } from '@/composables/useToast'
import { useAuthStore } from '@/stores/auth'

const toast = useToast()
const router = useRouter()
const authStore = useAuthStore()
const isOpen = ref(false)
const widgetRef = ref<HTMLElement | null>(null)
const loading = ref(false)
const balances = ref<BalanceInfo[]>([])

onClickOutside(widgetRef, () => {
  isOpen.value = false
})

function toggle() {
  isOpen.value = !isOpen.value
}

const workBalanceUsdt = computed(() => {
  const b = balances.value.find(b => b.type === 'work' && b.currency === 'USDT')
  return b ? b.amount : 0
})

async function loadBalances(options: { background?: boolean } = {}) {
  if (!options.background) loading.value = true
  try {
    const { data } = await financesService.listMyBalances()
    balances.value = data
  } catch (e) {
    if (!options.background) console.error('Failed to load balances', e)
  } finally {
    if (!options.background) loading.value = false
  }
}

function openTopup() {
  isOpen.value = false
  toast.info('Пополнение баланса пока недоступно')
}

// Work-balance USDT is the only amount eligible for withdrawal; ESCROW is
// frozen until orders settle and other currencies aren't payable this way.
const canWithdraw = computed(() => workBalanceUsdt.value > 0)

function openWithdraw() {
  if (!canWithdraw.value) return
  isOpen.value = false
  const role = authStore.userRole
  // Each role has its own finances/withdrawals page. Merchant's page supports
  // a `?create=1` shortcut that auto-opens the create-withdrawal modal;
  // trader/teamlead pages don't have that hook yet, so we just navigate.
  if (role === 'merchant') {
    router.push({ path: '/merchant/withdrawals', query: { create: '1' } })
  } else if (role === 'trader') {
    router.push({ path: '/trader/finances' })
  } else if (role === 'teamlead') {
    router.push({ path: '/teamlead/finances' })
  } else {
    toast.info('Вывод недоступен для вашей роли')
  }
}

const BALANCE_POLL_MS = 10_000
let intervalId: number

onMounted(() => {
  loadBalances()
  intervalId = window.setInterval(() => loadBalances({ background: true }), BALANCE_POLL_MS)
})

onUnmounted(() => {
  clearInterval(intervalId)
})
</script>

<style scoped>
.dropdown-enter-active,
.dropdown-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.dropdown-enter-from,
.dropdown-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
