<template>
  <div>
    <PageHeader title="Панель" />

    <div v-if="loading" class="py-16"><LoadingSpinner /></div>

    <template v-else>
      <div class="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
        <StatCard
          label="Общий заработок"
          :value="`${formatAmount(stats.total_earned_usdt)} USDT`"
          :icon="Wallet"
        />
        <StatCard
          label="Количество сделок"
          :value="stats.orders_count"
          :icon="Package"
        />
        <StatCard
          label="Активных связей"
          :value="stats.active_links_count"
          :icon="LinkIcon"
        />
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import StatCard from '@/components/ui/StatCard.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import { teamleadsService } from '@/api/services/teamleads.service'
import { useToast } from '@/composables/useToast'
import { formatAmount } from '@/utils/format'
import { Wallet, Package, Link as LinkIcon } from 'lucide-vue-next'

const toast = useToast()
const loading = ref(true)
const stats = reactive({
  total_earned_usdt: 0,
  orders_count: 0,
  active_links_count: 0,
})

async function load() {
  try {
    const { data } = await teamleadsService.getMyStats()
    stats.total_earned_usdt = Number(data.total_earned_usdt) || 0
    stats.orders_count = data.orders_count
    stats.active_links_count = data.active_links_count
  } catch {
    toast.error('Ошибка загрузки статистики')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
