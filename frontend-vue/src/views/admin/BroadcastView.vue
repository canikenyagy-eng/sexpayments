<template>
  <div>
    <PageHeader title="Рассылка" />

    <div>
      <!-- Compose -->
      <BaseCard title="Новая рассылка">
        <div class="space-y-4">
          <div>
            <textarea
              v-model="text"
              rows="7"
              maxlength="4096"
              placeholder="Текст сообщения для всех трейдеров… Эмодзи можно 🙂"
              class="w-full resize-y rounded-xl border border-border bg-bg-card px-3 py-2 text-sm text-text-main placeholder:text-text-muted focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
            />
            <div class="mt-1 text-right text-xs text-text-muted">{{ text.length }} / 4096</div>
          </div>

          <BaseSelect
            v-model="audience"
            label="Кому отправить"
            :options="audienceOptions"
          />

          <div class="flex items-center justify-between gap-3">
            <p class="text-sm text-text-muted">
              Получателей: <span class="font-bold text-text-main">{{ selectedCount }}</span>
            </p>
            <BaseButton
              variant="gold"
              :loading="sending"
              :disabled="!text.trim() || sending || (counts !== null && selectedCount === 0)"
              @click="send"
            >Отправить</BaseButton>
          </div>
        </div>
      </BaseCard>
    </div>

    <!-- History -->
    <div class="mt-6">
      <h3 class="mb-3 px-1 text-sm font-bold text-text-main">История рассылок</h3>
      <DataTable
        :columns="columns"
        :rows="history"
        :loading="loadingHistory"
        row-key="id"
        :show-pagination="false"
        empty-text="Рассылок ещё не было"
      >
        <template #cell-created_at="{ value }">{{ formatDate(value) }}</template>
        <template #cell-audience="{ value }">
          {{ value === 'all' ? 'Всем' : 'Кроме заблокированных' }}
        </template>
        <template #cell-text="{ value }">
          <span class="text-text-secondary">{{ truncate(value as string, 60) }}</span>
        </template>
        <template #cell-delivered="{ row }">
          <span class="font-bold text-text-main">{{ row.delivered }}</span>
          <span class="text-text-muted"> / {{ row.total_recipients }}</span>
          <span v-if="row.failed" class="text-status-danger"> · {{ row.failed }} ошиб.</span>
        </template>
        <template #cell-status="{ value }">
          <BaseBadge :color="statusColor(value as string)">{{ statusLabel(value as string) }}</BaseBadge>
        </template>
      </DataTable>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import { broadcastService, type Broadcast, type BroadcastAudience, type RecipientCount } from '@/api/services/broadcast.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { formatDate, truncate } from '@/utils/format'

const toast = useToast()
const { confirm } = useConfirm()

const text = ref('')
const audience = ref<BroadcastAudience>('all')
const sending = ref(false)

const counts = ref<RecipientCount | null>(null)
const selectedCount = computed(() =>
  audience.value === 'all' ? counts.value?.all ?? 0 : counts.value?.except_blocked ?? 0,
)
const audienceOptions = computed(() => [
  { value: 'all', label: `Всем (${counts.value?.all ?? '—'})` },
  { value: 'except_blocked', label: `Всем, кроме заблокированных (${counts.value?.except_blocked ?? '—'})` },
])

const history = ref<Broadcast[]>([])
const loadingHistory = ref(false)

const columns: Column[] = [
  { key: 'created_at', label: 'Дата' },
  { key: 'audience', label: 'Кому' },
  { key: 'text', label: 'Текст' },
  { key: 'delivered', label: 'Доставлено', align: 'right' },
  { key: 'status', label: 'Статус' },
]

type BadgeColor = 'success' | 'default' | 'gold' | 'danger' | 'info' | 'warning'
function statusLabel(s: string): string {
  const m: Record<string, string> = { pending: 'В очереди', sending: 'Отправка', done: 'Готово', failed: 'Ошибка' }
  return m[s] ?? s
}
function statusColor(s: string): BadgeColor {
  const m: Record<string, BadgeColor> = { pending: 'default', sending: 'warning', done: 'success', failed: 'danger' }
  return m[s] ?? 'default'
}

async function loadCounts() {
  try {
    const { data } = await broadcastService.recipientCount()
    counts.value = data
  } catch {
    // Advisory only (the backend re-derives the recipient set) — but tell the
    // admin, and leave the send button usable rather than dead-ended.
    toast.warning('Не удалось получить число получателей')
  }
}

async function loadHistory() {
  // Only show the table spinner on the first load — background polls (every 3s
  // while a broadcast is in flight) refresh rows in place without flicker.
  if (!history.value.length) loadingHistory.value = true
  try {
    const { data } = await broadcastService.history()
    history.value = data
    schedulePollIfActive()
  } catch {
    toast.error('Не удалось загрузить историю рассылок')
  } finally {
    loadingHistory.value = false
  }
}

let pollTimer: ReturnType<typeof setTimeout> | null = null
function schedulePollIfActive() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null }
  const active = history.value.some((b) => b.status === 'pending' || b.status === 'sending')
  if (active) pollTimer = setTimeout(loadHistory, 3000)
}

async function send() {
  const n = selectedCount.value
  const known = counts.value !== null
  const ok = await confirm({
    title: 'Отправить рассылку?',
    message: known
      ? `Сообщение получат ${n} трейдер(ов). Продолжить?`
      : 'Отправить сообщение всем трейдерам с привязанным Telegram?',
    confirmText: 'Отправить',
    cancelText: 'Отмена',
  })
  if (!ok) return
  sending.value = true
  try {
    await broadcastService.send({ text: text.value.trim(), audience: audience.value })
    toast.success(known ? `Рассылка запущена для ${n} трейдеров` : 'Рассылка запущена')
    text.value = ''
    await loadHistory()
  } catch (e: any) {
    toast.error(e.response?.data?.detail || 'Не удалось запустить рассылку')
  } finally {
    sending.value = false
  }
}

onMounted(() => {
  loadCounts()
  loadHistory()
})
onUnmounted(() => {
  if (pollTimer) clearTimeout(pollTimer)
})
</script>
