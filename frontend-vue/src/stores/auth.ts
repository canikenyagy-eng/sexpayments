import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import * as Sentry from '@sentry/vue'
import { authService } from '@/api/services/auth.service'
import { set401Suppressed } from '@/api/client'
import type { User, UserRole } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string | null>(localStorage.getItem('token'))
  const refreshToken = ref<string | null>(localStorage.getItem('refresh_token'))
  const originalToken = ref<string | null>(localStorage.getItem('original_token'))
  const originalRefreshToken = ref<string | null>(localStorage.getItem('original_refresh_token'))
  const user = ref<User | null>(null)

  const isAuthenticated = computed(() => !!token.value)
  const isImpersonating = computed(() => !!originalToken.value)
  const userRole = computed<UserRole | null>(() => user.value?.role ?? null)

  function setTokens(access: string, refresh: string) {
    token.value = access
    refreshToken.value = refresh
    localStorage.setItem('token', access)
    localStorage.setItem('refresh_token', refresh)
  }

  function setOriginalTokens(access: string, refresh: string) {
    originalToken.value = access
    originalRefreshToken.value = refresh
    localStorage.setItem('original_token', access)
    localStorage.setItem('original_refresh_token', refresh)
  }

  function clearOriginalTokens() {
    originalToken.value = null
    originalRefreshToken.value = null
    localStorage.removeItem('original_token')
    localStorage.removeItem('original_refresh_token')
  }

  function clearAuth() {
    token.value = null
    refreshToken.value = null
    user.value = null
    localStorage.removeItem('token')
    localStorage.removeItem('refresh_token')
    clearOriginalTokens()
  }

  async function fetchUser() {
    if (!token.value) return
    try {
      const { data } = await authService.getMe()
      user.value = data
      Sentry.setUser({ id: String(data.id), username: data.username })
    } catch {
      clearAuth()
    }
  }

  async function login(username: string, password: string, totp_code?: string) {
    const { data } = await authService.login(username, password, totp_code)
    setTokens(data.access_token, data.refresh_token)
    user.value = data.user
    Sentry.setUser({ id: String(data.user.id), username: data.user.username })
  }

  async function impersonate(targetUserId: number) {
    if (!isImpersonating.value && token.value && refreshToken.value) {
      setOriginalTokens(token.value, refreshToken.value)
    }
    const { data } = await authService.impersonate(targetUserId)
    setTokens(data.access_token, data.refresh_token)
    user.value = data.user
    Sentry.setUser({ id: String(data.user.id), username: data.user.username })
  }

  async function stopImpersonating() {
    if (!isImpersonating.value || !originalToken.value || !originalRefreshToken.value) return
    const savedRefresh = originalRefreshToken.value
    setTokens(originalToken.value, savedRefresh)
    clearOriginalTokens()
    set401Suppressed(true)
    try {
      const { data } = await authService.getMe()
      user.value = data
      Sentry.setUser({ id: String(data.id), username: data.username })
    } catch {
      try {
        const { data } = await authService.refresh(savedRefresh)
        setTokens(data.access_token, data.refresh_token)
        user.value = data.user
        Sentry.setUser({ id: String(data.user.id), username: data.user.username })
      } catch {
        clearAuth()
      }
    } finally {
      set401Suppressed(false)
    }
  }

  function logout() {
    if (refreshToken.value) {
      authService.logout(refreshToken.value).catch(() => {})
    }
    clearAuth()
    Sentry.setUser(null)
  }

  return { token, refreshToken, originalToken, originalRefreshToken, user, isAuthenticated, isImpersonating, userRole, login, impersonate, stopImpersonating, logout, fetchUser, setTokens }
})
