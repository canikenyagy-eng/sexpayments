<template>
  <div>
    <PageHeader title="Настройки площадки" />

    <BaseTabs v-model="activeTab" :tabs="tabs">
      <template #receipt-check>
        <ReceiptCheckSettingsPanel />
      </template>
      <template #premoderation>
        <PremoderationSettingsPanel />
      </template>
      <template #notifications>
        <NotificationsSettingsPanel />
      </template>
      <template #primetime>
        <PrimeTimeSettingsPanel />
      </template>
      <template #doliv>
        <DolivSettingsPanel />
      </template>
      <template #achievements>
        <AchievementsSettingsPanel />
      </template>
    </BaseTabs>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import BaseTabs from '@/components/ui/BaseTabs.vue'
import ReceiptCheckSettingsPanel from '@/components/platform-settings/ReceiptCheckSettingsPanel.vue'
import PremoderationSettingsPanel from '@/components/platform-settings/PremoderationSettingsPanel.vue'
import NotificationsSettingsPanel from '@/components/platform-settings/NotificationsSettingsPanel.vue'
import PrimeTimeSettingsPanel from '@/components/platform-settings/PrimeTimeSettingsPanel.vue'
import DolivSettingsPanel from '@/components/platform-settings/DolivSettingsPanel.vue'
import AchievementsSettingsPanel from '@/components/platform-settings/AchievementsSettingsPanel.vue'

const tabs = [
  { key: 'receipt-check', label: 'Проверка чеков' },
  { key: 'premoderation', label: 'Премодерация чеков' },
  { key: 'notifications', label: 'Уведомления' },
  { key: 'primetime', label: 'Prime-Time' },
  { key: 'doliv', label: 'Долив' },
  { key: 'achievements', label: 'Достижения' },
]
const activeTab = ref<string>('receipt-check')
</script>
