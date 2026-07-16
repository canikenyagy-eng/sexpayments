import { createApp } from 'vue'
import { createPinia } from 'pinia'
import './style.css'
import App from './App.vue'
import router from './router'

const app = createApp(App)

const sentryDsn = import.meta.env.VITE_SENTRY_DSN as string | undefined
const sentryEnv = (import.meta.env.VITE_SENTRY_ENVIRONMENT as string | undefined) || import.meta.env.MODE
const sentryRelease = import.meta.env.VITE_SENTRY_RELEASE as string | undefined
const tracesRate = Number(import.meta.env.VITE_SENTRY_TRACES_SAMPLE_RATE ?? '0')

if (sentryDsn) {
  import('@sentry/vue')
    .then(({ init, browserTracingIntegration }) => {
      init({
        app,
        dsn: sentryDsn,
        environment: sentryEnv,
        release: sentryRelease,
        integrations: [browserTracingIntegration({ router })],
        tracesSampleRate: Number.isFinite(tracesRate) ? tracesRate : 0,
        attachStacktrace: true,
        sendDefaultPii: false,
        ignoreErrors: [
          'ResizeObserver loop limit exceeded',
          'ResizeObserver loop completed with undelivered notifications',
          'Network Error',
          'Failed to fetch dynamically imported module',
          'error loading dynamically imported module',
          'Importing a module script failed',
          'Error invoking postEvent',
        ],
      })
    })
    .catch(err => {
      console.warn('[sentry] init failed', err)
    })
}

app.use(createPinia())
app.use(router)
app.mount('#app')
