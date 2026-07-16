import { computed, ref } from 'vue'
import { platformSettingsService } from '@/api/services/platformSettings.service'
import type { PrimeTimeState } from '@/types'

/**
 * Global Prime-Time state for the banner.
 *
 * Singleton: one poll loop (every 30s) + one 1s ticker drive every banner
 * instance (desktop sidebar + mobile header). `active` is computed locally from
 * `ends_at` so the strip disappears exactly at expiry without waiting for the
 * next poll. `start()` is idempotent.
 */
const state = ref<PrimeTimeState>({ active: false, points: null, ends_at: null })
const now = ref(Date.now())

let started = false

async function refresh(): Promise<void> {
  try {
    const { data } = await platformSettingsService.getPrimeTime()
    state.value = data
  } catch {
    // On error keep the last state; the banner just won't appear on a cold error.
  }
}

function start(): void {
  if (started) return
  started = true
  refresh()
  setInterval(refresh, 30_000)
  setInterval(() => { now.value = Date.now() }, 1_000)
}

const endsAtMs = computed(() =>
  state.value.ends_at ? new Date(state.value.ends_at).getTime() : 0,
)

// Active = backend says active AND not locally expired.
const active = computed(
  () => state.value.active && state.value.points != null && endsAtMs.value > now.value,
)

const points = computed(() => Number(state.value.points ?? 0))

const remainingLabel = computed(() => {
  const ms = endsAtMs.value - now.value
  if (ms <= 0) return ''
  const total = Math.floor(ms / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const pad = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`
})

export function usePrimeTime() {
  return { state, active, points, remainingLabel, refresh, start }
}
