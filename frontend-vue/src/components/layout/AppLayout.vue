<template>
  <div v-if="showAppLayout" :class="['min-h-screen bg-bg-main text-text-main', shellClass]">
    <div
      v-if="authStore.userRole !== 'admin'"
      class="pointer-events-none fixed inset-0 z-0 bg-[radial-gradient(circle_at_22%_4%,rgba(139,21,56,0.18),transparent_34rem),radial-gradient(circle_at_92%_10%,rgba(214,163,143,0.08),transparent_28rem),linear-gradient(180deg,#0D0D0D_0%,#121010_44%,#0D0D0D_100%)]"
    />
    <TheSidebar
      :open="sidebarOpen"
      :role="authStore.userRole"
      :username="authStore.user?.username ?? ''"
      :is-impersonating="authStore.isImpersonating"
      @navigate="sidebarOpen = false"
      @logout="handleLogout"
      @stop-impersonating="handleStopImpersonating"
    />

    <!-- Mobile topbar -->
    <div class="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-accent/15 bg-bg-main/85 px-4 shadow-[0_12px_40px_rgba(0,0,0,0.25)] backdrop-blur-xl lg:hidden">
      <div class="flex items-center">
        <button
          type="button"
          class="flex h-9 w-9 items-center justify-center rounded-lg text-text-main transition hover:bg-bg-hover"
          @click="sidebarOpen = true"
        >
          <Menu class="h-6 w-6" />
        </button>
        <div class="ml-3 text-lg font-black text-text-main">
          Sex<span class="text-accent">Payments</span>
        </div>
      </div>
      <div v-if="showBalanceWidget" class="w-48">
        <UserBalanceWidget />
      </div>
    </div>

    <main class="relative z-10 lg:ml-[232px]">
      <div
        v-if="showCommandBar"
        class="sticky top-0 z-20 hidden border-b border-accent/10 bg-bg-main/80 backdrop-blur-2xl lg:block"
      >
        <div class="mx-auto flex max-w-[1440px] items-center gap-4 px-4 py-3 sm:px-6 lg:px-8">
          <div class="flex min-w-0 flex-1 items-center gap-3">
            <span class="grid h-10 w-10 shrink-0 place-items-center rounded-xl border border-accent/15 bg-bg-surface/70 text-accent">
              <Command class="h-[18px] w-[18px]" />
            </span>
            <div class="min-w-0">
              <p class="sp-kicker">Командный центр</p>
              <p class="truncate text-sm font-black text-text-main">
                {{ roleLabel }} · {{ sectionLabel }}
              </p>
            </div>
          </div>

          <div class="sp-command-bar hidden h-10 w-[360px] items-center gap-2 rounded-xl border px-3 text-xs font-semibold text-text-muted xl:flex">
            <Search class="h-4 w-4 text-accent" />
            <span>Поиск операций, реквизитов, клиентов</span>
          </div>

          <div class="flex items-center gap-2 rounded-xl border border-status-success/20 bg-status-success/10 px-3 py-2 text-xs font-black text-status-success">
            <span class="sp-status-dot" />
            Контур активен
          </div>

          <div class="hidden items-center gap-2 rounded-xl border border-accent/15 bg-bg-surface/50 px-3 py-2 text-xs font-black text-text-secondary 2xl:flex">
            <ShieldCheck class="h-4 w-4 text-accent" />
            Приватный режим
          </div>
        </div>
      </div>

      <div class="mx-auto max-w-[1440px] px-4 sm:px-6 lg:px-8" :class="showCommandBar ? 'py-5' : 'py-6'">
        <router-view />
      </div>
    </main>

    <ToastContainer />
    <ConfirmModal />
  </div>

  <!-- Not authenticated → show router-view (login) -->
  <div v-else>
    <router-view />
    <ToastContainer />
    <ConfirmModal />
  </div>
</template>

<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import TheSidebar from './TheSidebar.vue'
import UserBalanceWidget from './UserBalanceWidget.vue'
import ToastContainer from '@/components/ui/ToastContainer.vue'
import ConfirmModal from '@/components/ui/ConfirmModal.vue'
import { Command, Menu, Search, ShieldCheck } from 'lucide-vue-next'

const authStore = useAuthStore()
const router = useRouter()
const route = useRoute()
const sidebarOpen = ref(false)

// Skip authenticated layout while navigating through guest-only routes (e.g. /login)
// to avoid flicker of the login page rendered inside the shrunken main area.
const showAppLayout = computed(
  () => authStore.isAuthenticated && !route.meta.guestOnly,
)

const showCommandBar = computed(() => showAppLayout.value)

const shellClass = computed(() =>
  authStore.userRole === 'admin' ? '' : 'account-branded-shell',
)

const balanceRoles = new Set(['merchant', 'trader', 'teamlead'])
const showBalanceWidget = computed(() =>
  balanceRoles.has(authStore.userRole ?? ''),
)

const roleLabel = computed(() => {
  const labels: Record<string, string> = {
    admin: 'Административный контур',
    support: 'Контур поддержки',
    merchant: 'Контур мерчанта',
    trader: 'Контур трейдера',
    teamlead: 'Контур тимлида',
  }
  return labels[authStore.userRole ?? ''] ?? 'Рабочий контур'
})

const sectionLabel = computed(() => {
  const path = route.path
  if (path.includes('/receipt-moderations')) return 'Проверка чеков'
  if (path.includes('/platform-settings') || path.includes('/configs')) return 'Настройки системы'
  if (path.includes('/cascade')) return 'Маршрутизация'
  if (path.includes('/api-logs') || path.includes('/callbacks')) return 'Интеграции'
  if (path.includes('/orders')) return 'Ордера'
  if (path.includes('/requisites')) return 'Реквизиты'
  if (path.includes('/finances')) return 'Финансы'
  if (path.includes('/payout')) return 'Выплаты'
  if (path.includes('/withdrawals')) return 'Выводы'
  if (path.includes('/disputes')) return 'Споры'
  if (path.includes('/traders')) return 'Трейдеры'
  if (path.includes('/teamleads')) return 'Тимлиды'
  if (path.includes('/merchants')) return 'Мерчанты'
  if (path.includes('/clients')) return 'Клиенты'
  if (path.includes('/rates')) return 'Курсы'
  if (path.includes('/broadcast')) return 'Рассылки'
  if (path.includes('/doliv')) return 'Доливы'
  return 'Панель управления'
})

function handleLogout() {
  sidebarOpen.value = false
  authStore.logout()
  router.push('/login')
}

async function handleStopImpersonating() {
  sidebarOpen.value = false
  await authStore.stopImpersonating()
  const homeMap: Record<string, string> = {
    admin: '/admin',
    support: '/support',
    merchant: '/merchant',
    trader: '/trader',
    teamlead: '/teamlead',
  }
  router.push(homeMap[authStore.userRole ?? ''] ?? '/login')
}
</script>
