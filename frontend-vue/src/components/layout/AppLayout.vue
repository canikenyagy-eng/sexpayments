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

    <!-- Main content -->
    <main class="relative z-10 lg:ml-[232px]">
      <div class="mx-auto max-w-[1440px] px-4 py-6 sm:px-6 lg:px-8">
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
import { Menu } from 'lucide-vue-next'

const authStore = useAuthStore()
const router = useRouter()
const route = useRoute()
const sidebarOpen = ref(false)

// Skip authenticated layout while navigating through guest-only routes (e.g. /login)
// to avoid flicker of the login page rendered inside the shrunken main area.
const showAppLayout = computed(
  () => authStore.isAuthenticated && !route.meta.guestOnly,
)

const shellClass = computed(() =>
  authStore.userRole === 'admin' ? '' : 'account-branded-shell',
)

const balanceRoles = new Set(['merchant', 'trader', 'teamlead'])
const showBalanceWidget = computed(() =>
  balanceRoles.has(authStore.userRole ?? ''),
)

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
