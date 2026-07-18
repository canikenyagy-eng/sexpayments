import axios, { type AxiosError, type InternalAxiosRequestConfig } from 'axios'
import router from '@/router'
// Static import (not dynamic): auth.ts is core and statically imported across the
// app, so a dynamic import here can't split it into its own chunk anyway (Vite
// warns it's ineffective). The client↔auth cycle is function-deferred on both
// sides (set401Suppressed is a hoisted fn; useAuthStore() is only called at
// runtime below), so ESM resolves it fine.
import { useAuthStore } from '@/stores/auth'
import { demoApiConfig } from '@/demo/api'

let suppress401 = false

export function set401Suppressed(value: boolean) {
  suppress401 = value
}

const baseURL = import.meta.env.VITE_API_URL || ''

const api = axios.create({
  baseURL,
  // Needed so the backend can set/refresh the `access_token` HttpOnly cookie
  // used to gate admin-only pages rendered server-side (e.g. Swagger UI
  // at `/api/docs` on production). Auth for XHR still goes via the Bearer
  // header below; the cookie is only consumed by server-rendered endpoints.
  withCredentials: true,
  ...demoApiConfig(),
})

api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

let refreshPromise: Promise<string | null> | null = null

async function refreshAccessToken(): Promise<string | null> {
  const rt = localStorage.getItem('refresh_token')
  if (!rt) return null
  try {
    const { data } = await axios.post(
      `${baseURL}/api/v1/auth/refresh`,
      { refresh_token: rt },
      { withCredentials: true },
    )
    const access = data?.access_token as string | undefined
    const refresh = data?.refresh_token as string | undefined
    if (!access || !refresh) return null

    localStorage.setItem('token', access)
    localStorage.setItem('refresh_token', refresh)

    try {
      // Keep the store's reactive token refs in sync (localStorage was already
      // updated above). Guarded: refresh runs at runtime (on a 401) when pinia
      // is active, but stay defensive in case it ever fires pre-bootstrap.
      useAuthStore().setTokens(access, refresh)
    } catch {
      /* pinia not active yet — tokens are already persisted to localStorage */
    }

    return access
  } catch {
    return null
  }
}

function hardLogout() {
  localStorage.removeItem('token')
  localStorage.removeItem('refresh_token')
  delete api.defaults.headers.common['Authorization']
  if (router.currentRoute.value.name !== 'login') {
    router.push({ name: 'login' })
  }
}

api.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const config = error.config as
      | (InternalAxiosRequestConfig & { _retry?: boolean })
      | undefined
    const status = error.response?.status

    if (status !== 401 || suppress401 || !config || config._retry) {
      return Promise.reject(error)
    }

    const url = typeof config.url === 'string' ? config.url : ''
    const isAuthCall = url.includes('/auth/refresh') || url.includes('/auth/login')
    if (isAuthCall) {
      hardLogout()
      return Promise.reject(error)
    }

    config._retry = true

    if (!refreshPromise) {
      refreshPromise = refreshAccessToken().finally(() => {
        refreshPromise = null
      })
    }

    const newToken = await refreshPromise
    if (!newToken) {
      hardLogout()
      return Promise.reject(error)
    }

    config.headers = config.headers ?? ({} as InternalAxiosRequestConfig['headers'])
    ;(config.headers as Record<string, string>).Authorization = `Bearer ${newToken}`
    return api.request(config)
  },
)

export default api
