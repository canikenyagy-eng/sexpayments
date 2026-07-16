<template>
  <div v-if="showAppLayout" class="min-h-screen bg-bg-main">
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
    <div class="sticky top-0 z-20 flex h-14 items-center justify-between border-b border-border bg-bg-main/95 px-4 backdrop-blur-md lg:hidden">
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
      <div v-if="authStore.userRole !== 'admin'" class="w-48">
        <UserBalanceWidget />
      </div>
    </div>

    <!-- Main content -->
    <main class="lg:ml-64">
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
    merchant: '/merchant',
    trader: '/trader',
    teamlead: '/teamlead',
  }
  router.push(homeMap[authStore.userRole ?? ''] ?? '/login')
}
</script>
