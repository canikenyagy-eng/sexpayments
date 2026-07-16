import { createRouter, createWebHistory } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import type { UserRole } from '@/types'

declare module 'vue-router' {
  interface RouteMeta {
    requiresAuth?: boolean
    guestOnly?: boolean
    roles?: UserRole[]
  }
}

const roleHome: Record<string, string> = {
  admin: '/admin',
  merchant: '/merchant',
  trader: '/trader',
  teamlead: '/teamlead',
}

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    // ── Public ───────────────────────────────────────
    {
      path: '/',
      name: 'home',
      component: () => import('@/views/public/LandingView.vue'),
    },

    // ── Auth ─────────────────────────────────────────
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/auth/LoginView.vue'),
      meta: { guestOnly: true },
    },

    // ── Admin ────────────────────────────────────────
    {
      path: '/admin',
      name: 'admin-dashboard',
      component: () => import('@/views/admin/DashboardView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/stats',
      name: 'admin-stats',
      component: () => import('@/views/admin/StatsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/users',
      name: 'admin-users',
      component: () => import('@/views/admin/UsersView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/merchants',
      name: 'admin-merchants',
      component: () => import('@/views/admin/MerchantsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/merchants/:id',
      name: 'admin-merchant-detail',
      component: () => import('@/views/admin/MerchantDetailView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/clients',
      name: 'admin-clients',
      component: () => import('@/views/admin/ClientsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/traders',
      name: 'admin-traders',
      component: () => import('@/views/admin/TradersView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/trader-groups',
      name: 'admin-trader-groups',
      component: () => import('@/views/admin/TraderGroupsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/orders',
      name: 'admin-orders',
      component: () => import('@/views/admin/OrdersView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/disputes',
      name: 'admin-disputes',
      component: () => import('@/views/admin/DisputesView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/requisites',
      name: 'admin-requisites',
      component: () => import('@/views/admin/RequisitesView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/withdrawals',
      name: 'admin-withdrawals',
      component: () => import('@/views/admin/WithdrawalsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/finances',
      name: 'admin-finances',
      component: () => import('@/views/admin/FinancesView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/payout-terminals',
      name: 'admin-payout-terminals',
      component: () => import('@/views/admin/PayoutTerminalsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/payout-terminals/:id',
      name: 'admin-payout-terminal-detail',
      component: () => import('@/views/admin/PayoutTerminalDetailView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/payouts',
      name: 'admin-payouts',
      component: () => import('@/views/admin/PayoutsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/configs',
      name: 'admin-configs',
      component: () => import('@/views/admin/ConfigsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/rates',
      name: 'admin-rates',
      component: () => import('@/views/admin/RatesView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/broadcast',
      name: 'admin-broadcast',
      component: () => import('@/views/admin/BroadcastView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/teamleads',
      name: 'admin-teamleads',
      component: () => import('@/views/admin/TeamleadsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/teamleads/:id',
      name: 'admin-teamlead-profile',
      component: () => import('@/views/admin/TeamleadProfileView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/api-logs',
      name: 'admin-api-logs',
      component: () => import('@/views/admin/ApiLogsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/callbacks',
      name: 'admin-callbacks',
      component: () => import('@/views/admin/CallbacksView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/cascade/providers',
      name: 'admin-cascade-providers',
      component: () => import('@/views/admin/CascadeProvidersView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/cascade/groups',
      name: 'admin-cascade-groups',
      component: () => import('@/views/admin/CascadeGroupsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/cascade/provider-requests',
      name: 'admin-cascade-provider-requests',
      component: () => import('@/views/admin/ProviderRequestsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/platform-settings',
      name: 'admin-platform-settings',
      component: () => import('@/views/admin/PlatformSettingsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/receipt-moderations',
      name: 'admin-receipt-moderations',
      component: () => import('@/views/admin/ReceiptModerationsView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },
    {
      path: '/admin/doliv',
      name: 'admin-doliv',
      component: () => import('@/views/admin/DolivView.vue'),
      meta: { requiresAuth: true, roles: ['admin'] },
    },

    // ── Trader ───────────────────────────────────────
    {
      path: '/trader',
      name: 'trader-dashboard',
      component: () => import('@/views/trader/DashboardView.vue'),
      meta: { requiresAuth: true, roles: ['trader'] },
    },
    {
      path: '/trader/orders',
      name: 'trader-orders',
      component: () => import('@/views/trader/ActiveOrdersView.vue'),
      meta: { requiresAuth: true, roles: ['trader'] },
    },
    {
      path: '/trader/requisites',
      name: 'trader-requisites',
      component: () => import('@/views/trader/RequisitesView.vue'),
      meta: { requiresAuth: true, roles: ['trader'] },
    },
    {
      path: '/trader/finances',
      name: 'trader-finances',
      component: () => import('@/views/trader/FinancesView.vue'),
      meta: { requiresAuth: true, roles: ['trader'] },
    },
    {
      path: '/trader/disputes',
      name: 'trader-disputes',
      component: () => import('@/views/trader/DisputesView.vue'),
      meta: { requiresAuth: true, roles: ['trader'] },
    },
    {
      path: '/trader/payouts',
      name: 'trader-payouts',
      component: () => import('@/views/trader/PayoutsView.vue'),
      meta: { requiresAuth: true, roles: ['trader'] },
    },
    {
      path: '/trader/doliv',
      name: 'trader-doliv',
      component: () => import('@/views/trader/DolivView.vue'),
      meta: { requiresAuth: true, roles: ['trader'] },
    },

    // ── Teamlead ─────────────────────────────────────
    {
      path: '/teamlead',
      name: 'teamlead-dashboard',
      component: () => import('@/views/teamlead/DashboardView.vue'),
      meta: { requiresAuth: true, roles: ['teamlead'] },
    },
    {
      path: '/teamlead/links',
      name: 'teamlead-links',
      component: () => import('@/views/teamlead/LinksView.vue'),
      meta: { requiresAuth: true, roles: ['teamlead'] },
    },
    {
      path: '/teamlead/finances',
      name: 'teamlead-finances',
      component: () => import('@/views/teamlead/FinancesView.vue'),
      meta: { requiresAuth: true, roles: ['teamlead'] },
    },

    // ── Merchant ─────────────────────────────────────
    {
      path: '/merchant',
      name: 'merchant-dashboard',
      component: () => import('@/views/merchant/DashboardView.vue'),
      meta: { requiresAuth: true, roles: ['merchant'] },
    },
    {
      path: '/merchant/orders',
      name: 'merchant-orders',
      component: () => import('@/views/merchant/OrdersView.vue'),
      meta: { requiresAuth: true, roles: ['merchant'] },
    },
    {
      path: '/merchant/payouts',
      name: 'merchant-payouts',
      component: () => import('@/views/merchant/PayoutsView.vue'),
      meta: { requiresAuth: true, roles: ['merchant'] },
    },
    {
      path: '/merchant/withdrawals',
      name: 'merchant-withdrawals',
      component: () => import('@/views/merchant/WithdrawalsView.vue'),
      meta: { requiresAuth: true, roles: ['merchant'] },
    },
    {
      path: '/merchant/disputes',
      name: 'merchant-disputes',
      component: () => import('@/views/merchant/DisputesView.vue'),
      meta: { requiresAuth: true, roles: ['merchant'] },
    },
    {
      path: '/merchant/terminals',
      name: 'merchant-terminals',
      component: () => import('@/views/merchant/MerchantsView.vue'),
      meta: { requiresAuth: true, roles: ['merchant'] },
    },
    {
      path: '/merchant/terminals/:id',
      name: 'merchant-terminal-detail',
      component: () => import('@/views/merchant/MerchantTerminalView.vue'),
      meta: { requiresAuth: true, roles: ['merchant'] },
    },
    {
      path: '/merchant/settings',
      name: 'merchant-settings',
      component: () => import('@/views/merchant/SettingsView.vue'),
      meta: { requiresAuth: true, roles: ['merchant'] },
    },

    // ── Shared ───────────────────────────────────────
    {
      path: '/profile',
      name: 'profile',
      component: () => import('@/views/shared/ProfileView.vue'),
      meta: { requiresAuth: true },
    },

    // ── 404 catch-all ────────────────────────────────
    {
      path: '/:pathMatch(.*)*',
      redirect: '/',
    },
  ],
})

router.beforeEach(async (to: any, _from: any, next: any) => {
  const auth = useAuthStore()

  if (auth.token && !auth.user) {
    await auth.fetchUser()
  }

  if (to.name === 'home' && auth.isAuthenticated) {
    return next(roleHome[auth.userRole ?? ''] ?? '/login')
  }

  if (to.meta.requiresAuth && !auth.isAuthenticated) {
    return next('/login')
  }

  if (to.meta.guestOnly && auth.isAuthenticated) {
    return next('/')
  }

  if (to.meta.roles && auth.userRole && !to.meta.roles.includes(auth.userRole)) {
    return next('/')
  }

  next()
})

router.onError((error: Error, to: any) => {
  const errors = [
    'Failed to fetch dynamically imported module',
    'error loading dynamically imported module',
    'Importing a module script failed',
  ]

  if (errors.some((e) => error.message.includes(e))) {
    const retryKey = `retry-${to.fullPath}`
    const lastRetry = Number(sessionStorage.getItem(retryKey) || 0)

    if (Date.now() - lastRetry > 10000) {
      sessionStorage.setItem(retryKey, Date.now().toString())
      window.location.href = to.fullPath
    }
  }
})

export default router
