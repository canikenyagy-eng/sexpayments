<template>
  <aside
    :class="[
      'fixed inset-y-0 left-0 z-40 flex w-64 flex-col border-r border-border bg-bg-surface transition-transform duration-300 lg:translate-x-0',
      open ? 'translate-x-0' : '-translate-x-full',
    ]"
  >
    <!-- Logo -->
    <div class="flex h-16 items-center gap-3 border-b border-border px-5">
      <img src="/logos/logo.svg" alt="Логотип SexPayments" class="h-9 w-9" />
      <div class="text-xl font-black leading-none text-text-main">
        Sex<span class="text-accent">Payments</span>
      </div>
    </div>

    <!-- Prime-Time (sidebar card; shown on desktop and in the mobile drawer) -->
    <PrimeTimeBanner class="mx-3 mt-3" />

    <!-- Navigation -->
    <nav class="flex-1 overflow-y-auto px-3 py-4">
      <div v-if="role !== 'admin'" class="mb-1">
        <UserBalanceWidget />
        <div class="my-2 mx-2 border-t border-border/50"></div>
      </div>

      <div v-for="(group, index) in navGroups" :key="index" class="mb-1">
        <router-link
          v-for="item in group.items"
          :key="item.to"
          :to="item.to"
          class="mb-0.5 flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-bold text-text-secondary no-underline transition-colors hover:bg-bg-hover hover:text-text-main"
          :class="{ '!bg-accent/10 !text-accent': isActive(item.to) }"
          @click="$emit('navigate')"
        >
          <component :is="item.icon" class="h-5 w-5 shrink-0" />
          <span class="flex-1 truncate">{{ item.label }}</span>
          <span
            v-if="shouldShowBadge(item)"
            class="ml-auto inline-flex h-5 min-w-[20px] items-center justify-center rounded-full px-1.5 text-[11px] font-black leading-none"
            :class="[
              getBadge(item) > 0
                ? 'bg-accent/15 text-accent'
                : 'bg-status-warning/15 text-status-warning',
              isActive(item.to) && getBadge(item) > 0
                ? '!bg-accent !text-[#110c09]'
                : '',
            ]"
          >
            {{ formatBadge(getBadge(item)) }}
          </span>
        </router-link>
        
        <!-- Divider between groups -->
        <div v-if="index !== navGroups.length - 1" class="my-2 border-t border-border/50 mx-2"></div>
      </div>
    </nav>

    <!-- User block -->
    <div class="border-t border-border p-3">
      <button
        v-if="isImpersonating"
        type="button"
        class="mb-3 flex w-full items-center gap-2 rounded-xl px-3 py-2 text-left text-sm font-bold text-status-warning transition hover:bg-status-warning/10"
        @click="$emit('stopImpersonating')"
      >
        <Undo2 class="h-4 w-4 shrink-0" />
        Выйти в админку
      </button>

      <div class="relative" ref="profileDropdownRef">
        <button
          type="button"
          class="flex w-full items-center gap-3 rounded-xl p-2 transition hover:bg-bg-hover"
          @click="isDropdownOpen = !isDropdownOpen"
        >
          <div class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent/15 text-xs font-black text-accent">
            {{ userInitial }}
          </div>
          <div class="min-w-0 flex-1 text-left">
            <p class="truncate text-sm font-bold text-text-main">{{ username }}</p>
            <p class="text-xs text-text-muted">{{ roleLabel }}</p>
          </div>
          <ChevronUp v-if="isDropdownOpen" class="h-4 w-4 shrink-0 text-text-muted" />
          <ChevronDown v-else class="h-4 w-4 shrink-0 text-text-muted" />
        </button>

        <!-- Dropdown -->
        <Transition name="fade">
          <div
            v-if="isDropdownOpen"
            class="absolute bottom-full left-0 mb-2 w-full rounded-xl border border-border bg-bg-surface p-1 shadow-lg"
          >
            <button
              type="button"
              class="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-bold text-text-secondary transition hover:bg-bg-hover hover:text-text-main"
              @click="goToProfile"
            >
              <User class="h-4 w-4" />
              Профиль
            </button>
            <button
              type="button"
              class="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm font-bold text-status-danger transition hover:bg-status-danger/10"
              @click="handleLogout"
            >
              <LogOut class="h-4 w-4" />
              Выход
            </button>
          </div>
        </Transition>
      </div>
    </div>
  </aside>

  <!-- Overlay -->
  <Transition name="fade">
    <div
      v-if="open"
      class="fixed inset-0 z-30 bg-black/50 lg:hidden"
      @click="$emit('navigate')"
      @touchmove.prevent
      @wheel.prevent
    />
  </Transition>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { onClickOutside, useScrollLock, useWindowSize } from '@vueuse/core'
import type { UserRole } from '@/types'
import { statsService, type ActiveStats } from '@/api/services/stats.service'
import UserBalanceWidget from './UserBalanceWidget.vue'
import PrimeTimeBanner from './PrimeTimeBanner.vue'
import {
  LayoutDashboard,
  BarChart3,
  Users,
  Store,
  TrendingUp,
  Settings,
  Package,
  CreditCard,
  Wallet,
  LineChart,
  Send,
  Link as LinkIcon,
  Layers,
  AlertTriangle,
  FileText,
  Webhook,
  Network,
  ArrowLeftRight,
  LogOut,
  ChevronDown,
  ChevronUp,
  User,
  Undo2,
  ShieldCheck,
  Droplet,
  Contact
} from 'lucide-vue-next'

type BadgeKey = keyof ActiveStats

interface NavItem {
  icon: Component
  label: string
  to: string
  badge?: BadgeKey
}

interface NavGroup {
  title: string
  items: NavItem[]
}

const props = defineProps<{
  open: boolean
  role: UserRole | null
  username: string
  isImpersonating: boolean
}>()

const emit = defineEmits<{ navigate: []; logout: []; stopImpersonating: [] }>()

const route = useRoute()
const router = useRouter()

const isDropdownOpen = ref(false)
const profileDropdownRef = ref<HTMLElement | null>(null)

const isLocked = useScrollLock(typeof document !== 'undefined' ? document.body : null)
const { width } = useWindowSize()

watch([() => props.open, width], ([isOpen, w]) => {
  // Lock scroll only on mobile screens (lg: 1024px)
  if (w < 1024) {
    isLocked.value = isOpen
  } else {
    isLocked.value = false
  }
}, { immediate: true })

onClickOutside(profileDropdownRef, () => {
  isDropdownOpen.value = false
})

function goToProfile() {
  isDropdownOpen.value = false
  router.push('/profile')
  emit('navigate')
}

function handleLogout() {
  isDropdownOpen.value = false
  emit('logout')
}

const roleLabel = computed(() => {
  const map: Record<string, string> = {
    admin: 'Администратор',
    merchant: 'Мерчант',
    trader: 'Трейдер',
    teamlead: 'Тимлид',
  }
  return props.role ? map[props.role] ?? props.role : ''
})

const userInitial = computed(() => props.username?.charAt(0).toUpperCase() ?? '?')

const navGroups = computed<NavGroup[]>(() => {
  if (props.role === 'admin') {
    return [
      {
        title: 'Обзор',
        items: [
          { icon: LayoutDashboard, label: 'Панель', to: '/admin' },
          { icon: BarChart3, label: 'Статистика', to: '/admin/stats' },
        ],
      },
      {
        title: 'Операции',
        items: [
          { icon: Package, label: 'Сделки', to: '/admin/orders', badge: 'orders_active' },
          { icon: ArrowLeftRight, label: 'Выплаты', to: '/admin/payouts' },
          { icon: CreditCard, label: 'Реквизиты', to: '/admin/requisites', badge: 'requisites_traffic_active' },
          { icon: AlertTriangle, label: 'Споры', to: '/admin/disputes', badge: 'active_disputes' },
          { icon: Contact, label: 'Клиенты', to: '/admin/clients' },
          { icon: ShieldCheck, label: 'Чеки', to: '/admin/receipt-moderations' },
          { icon: Droplet, label: 'Доливы', to: '/admin/doliv' },
        ],
      },
      {
        title: 'Участники',
        items: [
          { icon: Users, label: 'Пользователи', to: '/admin/users' },
          { icon: TrendingUp, label: 'Трейдеры', to: '/admin/traders' },
          { icon: Store, label: 'Мерчанты', to: '/admin/merchants' },
          { icon: CreditCard, label: 'Выплатные терминалы', to: '/admin/payout-terminals' },
          { icon: Layers, label: 'Группы', to: '/admin/trader-groups' },
          { icon: LinkIcon, label: 'Тимлиды', to: '/admin/teamleads' },
        ],
      },
      {
        title: 'Балансы',
        items: [
          { icon: Wallet, label: 'Финансы', to: '/admin/finances' },
          { icon: Wallet, label: 'Выводы', to: '/admin/withdrawals', badge: 'pending_withdrawals' },
        ],
      },
      {
        title: 'Настройки',
        items: [
          { icon: Settings, label: 'Конфиги', to: '/admin/configs' },
          { icon: LineChart, label: 'Курсы', to: '/admin/rates' },
          { icon: Send, label: 'Рассылка', to: '/admin/broadcast' },
          { icon: Settings, label: 'Настройки площадки', to: '/admin/platform-settings' },
        ],
      },
      {
        title: 'Каскад',
        items: [
          { icon: Network, label: 'Провайдеры', to: '/admin/cascade/providers' },
          { icon: Layers, label: 'Группы каскада', to: '/admin/cascade/groups' },
          { icon: ArrowLeftRight, label: 'Запросы провайдеров', to: '/admin/cascade/provider-requests' },
        ],
      },
      {
        title: 'Система',
        items: [
          { icon: FileText, label: 'Запросы интеграции', to: '/admin/api-logs' },
          { icon: Webhook, label: 'Колбэки', to: '/admin/callbacks' },
        ],
      },
    ]
  }

  if (props.role === 'trader') {
    return [
      {
        title: 'Обзор',
        items: [{ icon: LayoutDashboard, label: 'Панель', to: '/trader' }],
      },
      {
        title: 'Работа',
        items: [
          { icon: Package, label: 'Ордера', to: '/trader/orders', badge: 'orders_active' },
          {
            icon: CreditCard,
            label: 'Реквизиты',
            to: '/trader/requisites',
            badge: 'requisites_traffic_active',
          },
          { icon: Wallet, label: 'Финансы', to: '/trader/finances' },
          { icon: ArrowLeftRight, label: 'Выплаты', to: '/trader/payouts' },
          { icon: Droplet, label: 'Доливы', to: '/trader/doliv' },
          { icon: AlertTriangle, label: 'Споры', to: '/trader/disputes', badge: 'active_disputes' },
        ],
      },
    ]
  }

  if (props.role === 'teamlead') {
    return [
      {
        title: 'Обзор',
        items: [{ icon: LayoutDashboard, label: 'Панель', to: '/teamlead' }],
      },
      {
        title: 'Данные',
        items: [
          { icon: LinkIcon, label: 'Связи', to: '/teamlead/links' },
          { icon: Wallet, label: 'Финансы', to: '/teamlead/finances' },
        ],
      },
    ]
  }

  if (props.role === 'merchant') {
    return [
      {
        title: 'Обзор',
        items: [{ icon: LayoutDashboard, label: 'Панель', to: '/merchant' }],
      },
      {
        title: 'Операции',
        items: [
          { icon: Store, label: 'Терминалы', to: '/merchant/terminals' },
          { icon: Package, label: 'Ордера', to: '/merchant/orders', badge: 'orders_active' },
          { icon: ArrowLeftRight, label: 'Выплаты', to: '/merchant/payouts' },
          { icon: Wallet, label: 'Выводы', to: '/merchant/withdrawals', badge: 'pending_withdrawals' },
          { icon: AlertTriangle, label: 'Споры', to: '/merchant/disputes', badge: 'active_disputes' },
        ],
      },
      {
        title: 'Аккаунт',
        items: [
          { icon: Settings, label: 'Настройки', to: '/merchant/settings' },
        ],
      },
    ]
  }

  return []
})

function isActive(path: string): boolean {
  if (path === '/admin' || path === '/trader' || path === '/teamlead' || path === '/merchant') {
    return route.path === path
  }
  return route.path.startsWith(path)
}

const activeStats = ref<ActiveStats>({
  orders_active: 0,
  active_disputes: 0,
  pending_withdrawals: 0,
  requisites_traffic_active: 0,
})

function getBadge(item: NavItem): number {
  if (!item.badge) return 0
  return activeStats.value[item.badge] ?? 0
}


function isAlwaysVisibleBadge(badge: BadgeKey | undefined): boolean {
  return badge === 'requisites_traffic_active'
}

function shouldShowBadge(item: NavItem): boolean {
  if (!item.badge) return false
  if (isAlwaysVisibleBadge(item.badge)) return true
  return getBadge(item) > 0
}

function formatBadge(value: number): string {
  if (value > 99) return '99+'
  return String(value)
}

let pollTimer: ReturnType<typeof setInterval> | null = null

async function fetchActiveStats() {
  if (!props.role) return
  try {
    const { data } = await statsService.getActiveStats()
    activeStats.value = data
  } catch {
    // Silent fail keeps the sidebar usable when stats endpoint is briefly
    // unavailable; counters just stay at their last known values.
  }
}

watch(
  () => props.role,
  (role) => {
    if (role) fetchActiveStats()
  },
  { immediate: true },
)

watch(
  () => route.fullPath,
  () => {
    if (props.role) fetchActiveStats()
  },
)

onMounted(() => {
  pollTimer = setInterval(() => {
    if (props.role) fetchActiveStats()
  }, 30_000)
})

onBeforeUnmount(() => {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
})
</script>

<style scoped>
.fade-enter-active, .fade-leave-active { transition: opacity 0.2s; }
.fade-enter-from, .fade-leave-to { opacity: 0; }
</style>
