import { ref } from 'vue'
import { statsService, type ActiveStats } from '@/api/services/stats.service'

const stats = ref<ActiveStats>({
  orders_active: 0,
  active_disputes: 0,
  pending_withdrawals: 0,
  requisites_traffic_active: 0,
})

let inflight: Promise<void> | null = null

async function refresh(): Promise<void> {
  if (inflight) return inflight
  inflight = (async () => {
    try {
      const { data } = await statsService.getActiveStats()
      stats.value = data
    } catch {
    } finally {
      inflight = null
    }
  })()
  return inflight
}

export function useActiveStats() {
  return { stats, refresh }
}
