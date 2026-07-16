<template>
  <div>
    <h4 class="mb-2 text-sm font-bold text-accent">Доказательства</h4>

    <div v-if="loading" class="py-3 text-center"><LoadingSpinner /></div>

    <p v-else-if="!items.length" class="text-sm text-text-muted">
      Файлы не прикреплены.
    </p>

    <ul v-else class="space-y-1.5">
      <li
        v-for="item in items"
        :key="item.uuid"
        class="flex items-center justify-between gap-3 rounded-lg bg-bg-card px-3 py-2"
      >
        <div class="min-w-0">
          <p class="truncate text-sm text-text-main">{{ item.filename || 'файл' }}</p>
          <p class="text-xs text-text-muted">
            <template v-if="showSource">{{ sourceLabel(item.source) }} · </template>{{ item.created_at ? formatDate(item.created_at) : '' }}
          </p>
        </div>
        <BaseButton
          variant="ghost"
          size="sm"
          :loading="busyUuid === item.uuid"
          @click="open(item)"
        >
          <FileText class="mr-1 h-4 w-4" /> Открыть
        </BaseButton>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { FileText } from 'lucide-vue-next'
import BaseButton from '@/components/ui/BaseButton.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import { downloadReceipt, preOpenReceiptTab } from '@/utils/receipt'
import { formatDate } from '@/utils/format'
import { useToast } from '@/composables/useToast'
import type { DisputeEvidenceItem } from '@/types'

const props = withDefaults(defineProps<{
  items: DisputeEvidenceItem[]
  loading?: boolean
  // Role-specific download URL builder for one evidence file.
  resolveUrl: (item: DisputeEvidenceItem) => string
  // Whether to show the uploader source label (e.g. «Система»). Hidden for the
  // trader — they don't need to know who attached the check.
  showSource?: boolean
}>(), { showSource: true })

const toast = useToast()
const busyUuid = ref<string | null>(null)

const SOURCE_LABELS: Record<string, string> = {
  merchant: 'Мерчант',
  dispute_bot: 'Диспут-бот',
  support_bot: 'Премодерация',
  system: 'Система',
  trader: 'Трейдер',
}

function sourceLabel(source: string): string {
  return SOURCE_LABELS[source] ?? source
}

async function open(item: DisputeEvidenceItem) {
  if (busyUuid.value) return
  // Pre-open a tab synchronously on click so mobile browsers don't block it.
  const pre = preOpenReceiptTab()
  busyUuid.value = item.uuid
  try {
    await downloadReceipt(props.resolveUrl(item), pre)
  } catch (e: any) {
    toast.error(e?.message || 'Не удалось открыть файл')
  } finally {
    busyUuid.value = null
  }
}
</script>
