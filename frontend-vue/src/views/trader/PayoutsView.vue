<template>
  <div>
    <PageHeader title="Выплаты" />

    <BaseTabs v-model="tab" :tabs="tabs">
      <!-- ── Мои выплаты ── -->
      <template #mine>
        <DataTable
          :columns="mineColumns"
          :rows="mine"
          :loading="loadingMine"
          row-key="id"
          clickable
          :show-pagination="false"
          empty-text="Пока пусто."
          @row-click="openInfo($event as TraderPayout)"
        >
          <template #cell-id="{ value }">
            <UuidDisplay :value="String(value)" />
          </template>
          <template #cell-amount="{ row }">
            <span class="font-bold text-text-main">{{ formatAmount((row as any).amount) }}</span>
            <span class="ml-1 text-text-muted">{{ (row as any).currency }}</span>
          </template>
          <template #cell-amount_usdt="{ value }">
            <span class="text-text-main">{{ value != null ? formatAmount(Number(value)) : '—' }}</span>
          </template>
          <template #cell-payment_method="{ value }">
            <MethodBadge :method="value" />
          </template>
          <template #cell-req_number="{ row }">
            <span v-if="(row as any).req_number" class="font-mono text-xs text-text-main">{{ (row as any).req_number }}</span>
            <span v-else class="text-text-muted">—</span>
          </template>
          <template #cell-trader_fee_usdt="{ value }">
            <span class="font-mono text-text-main">{{ value != null ? formatAmount(Number(value)) : '—' }}</span>
          </template>
          <template #cell-status="{ row }">
            <StatusBadge :status="(row as any).status" :expires-at="(row as any).claim_expires_at" />
          </template>
          <template #cell-created_at="{ value }">
            {{ formatDate(value) }}
          </template>
          <template #actions="{ row }">
            <BaseButton
              v-if="canUpload((row as any).status)"
              variant="dark"
              size="sm"
              @click.stop="openUpload(row as TraderPayout)"
            >
              Чек
            </BaseButton>
          </template>
        </DataTable>
      </template>

      <!-- ── Доступные ── -->
      <template #pool>
        <DataTable
          :columns="poolColumns"
          :rows="pool"
          :loading="loadingPool"
          row-key="id"
          :show-pagination="false"
          empty-text="Нет доступных выплат."
        >
          <template #cell-amount="{ row }">
            <span class="font-bold text-text-main">{{ formatAmount((row as any).amount) }}</span>
            <span class="ml-1 text-text-muted">{{ (row as any).currency }}</span>
          </template>
          <template #cell-amount_usdt="{ value }">
            <span class="text-text-main">{{ value != null ? formatAmount(Number(value)) : '—' }}</span>
          </template>
          <template #cell-payment_method="{ value }">
            <MethodBadge :method="value" />
          </template>
          <template #cell-expires_at="{ value }">
            <span class="text-xs text-text-muted">{{ value ? formatDate(value) : '—' }}</span>
          </template>
          <template #actions="{ row }">
            <BaseButton variant="gold" size="sm" :loading="busy" @click.stop="claim((row as any).id)">
              Взять
            </BaseButton>
          </template>
        </DataTable>
      </template>

    </BaseTabs>

    <!-- Payout info modal -->
    <BaseModal v-model="showInfo" :title="`Выплата`" size="lg">
      <div v-if="selected" class="grid grid-cols-1 gap-3 text-sm sm:grid-cols-2">
        <div class="sm:col-span-2">
          <span class="text-text-muted">UUID:</span>
          <UuidDisplay :value="selected.id" :truncate="false" show-icon class="ml-1" />
        </div>
        <div>
          <span class="text-text-muted">Сумма:</span>
          <span class="ml-2 font-bold text-text-main">{{ formatAmount(Number(selected.amount)) }} {{ selected.currency }}</span>
        </div>
        <div>
          <span class="text-text-muted">USDT:</span>
          <span class="ml-2 text-text-main">{{ selected.amount_usdt != null ? formatAmount(Number(selected.amount_usdt)) : '—' }}</span>
        </div>
        <div>
          <span class="text-text-muted">Прибыль:</span>
          <span class="ml-2 text-status-success">{{ selected.trader_fee_usdt != null ? formatAmount(Number(selected.trader_fee_usdt)) + ' USDT' : '—' }}</span>
        </div>
        <div>
          <span class="text-text-muted">Метод:</span>
          <span class="ml-2"><MethodBadge :method="selected.payment_method" /></span>
        </div>
        <div>
          <span class="text-text-muted">Статус:</span>
          <span class="ml-2"><StatusBadge :status="selected.status" :expires-at="selected.claim_expires_at" /></span>
        </div>
        <div>
          <span class="text-text-muted">Получатель:</span>
          <span class="ml-2 text-text-main">{{ selected.req_holder || '—' }}</span>
        </div>
        <div class="sm:col-span-2">
          <span class="text-text-muted">Реквизит:</span>
          <span class="ml-2 font-mono text-text-main">{{ selected.req_number || '—' }}</span>
          <span v-if="selected.req_extra" class="ml-1 text-text-muted">· {{ selected.req_extra }}</span>
        </div>
        <div>
          <span class="text-text-muted">Создана:</span>
          <span class="ml-2 text-text-main">{{ formatDate(selected.created_at) }}</span>
        </div>
        <div>
          <span class="text-text-muted">Завершена:</span>
          <span class="ml-2 text-text-main">{{ selected.completed_at ? formatDate(selected.completed_at) : '—' }}</span>
        </div>
      </div>
      <template #footer>
        <BaseButton
          v-if="selected && canUpload(selected.status)"
          variant="gold"
          @click="openUpload(selected)"
        >
          Загрузить чек
        </BaseButton>
        <BaseButton variant="dark" @click="showInfo = false">Закрыть</BaseButton>
      </template>
    </BaseModal>

    <!-- Upload receipt modal -->
    <BaseModal v-model="showUpload" title="Чек об оплате">
      <div class="space-y-3">
        <p class="text-sm text-text-muted">Можно закрыть несколькими частичными платежами.</p>
        <BaseInput v-model="uploadAmount" label="Сумма этого платежа" type="number" />
        <input type="file" class="text-sm" @change="onFile" />
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showUpload = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="busy" @click="doUpload">Отправить</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import BaseTabs from '@/components/ui/BaseTabs.vue'
import type { Tab } from '@/components/ui/BaseTabs.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import { payoutsService } from '@/api/services/payouts.service'
import { useToast } from '@/composables/useToast'
import { formatAmount, formatDate } from '@/utils/format'
import type { TraderPayout, TraderPayoutPoolItem } from '@/types'

const toast = useToast()
const tab = ref('mine')
const loadingPool = ref(false)
const loadingMine = ref(false)
const busy = ref(false)
const pool = ref<TraderPayoutPoolItem[]>([])
const mine = ref<TraderPayout[]>([])

const showInfo = ref(false)
const selected = ref<TraderPayout | null>(null)

const showUpload = ref(false)
const activeUuid = ref('')
const uploadAmount = ref('')
const uploadFile = ref<File | null>(null)

const tabs = computed<Tab[]>(() => [
  { key: 'mine', label: 'Мои выплаты' },
  { key: 'pool', label: 'Доступные', count: pool.value.length },
])

const mineColumns: Column[] = [
  { key: 'id', label: 'UUID' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'amount_usdt', label: 'USDT', align: 'right' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'req_number', label: 'Реквизит' },
  { key: 'trader_fee_usdt', label: 'Прибыль', align: 'right' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Создана' },
]

const poolColumns: Column[] = [
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'amount_usdt', label: 'USDT', align: 'right' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'expires_at', label: 'Истекает' },
]

function canUpload(s: string) {
  return s === 'claimed' || s === 'awaiting_check'
}

async function loadPool() {
  loadingPool.value = true
  try { pool.value = (await payoutsService.pool({ limit: 100 })).data }
  catch { toast.error('Ошибка загрузки пула') }
  finally { loadingPool.value = false }
}

async function loadMine() {
  loadingMine.value = true
  try { mine.value = (await payoutsService.mine({ limit: 100 })).data }
  catch { toast.error('Ошибка загрузки') }
  finally { loadingMine.value = false }
}

async function claim(uuid: string) {
  busy.value = true
  try {
    await payoutsService.claim(uuid)
    toast.success('Заявка взята')
    await Promise.all([loadPool(), loadMine()])
    tab.value = 'mine'
  } catch (e: any) {
    toast.error(e.response?.data?.detail || e.response?.data?.message || 'Не удалось взять')
  } finally {
    busy.value = false
  }
}

function openInfo(p: TraderPayout) {
  selected.value = p
  showInfo.value = true
}

function openUpload(p: TraderPayout) {
  activeUuid.value = p.id
  uploadAmount.value = String(p.amount)
  uploadFile.value = null
  showUpload.value = true
}

function onFile(e: Event) {
  uploadFile.value = (e.target as HTMLInputElement).files?.[0] ?? null
}

async function doUpload() {
  if (!uploadFile.value) { toast.error('Выберите файл'); return }
  busy.value = true
  try {
    await payoutsService.uploadReceipt(activeUuid.value, Number(uploadAmount.value), uploadFile.value)
    toast.success('Чек отправлен')
    showUpload.value = false
    showInfo.value = false
    loadMine()
  } catch (e: any) {
    toast.error(e.response?.data?.detail || e.response?.data?.message || 'Ошибка отправки')
  } finally {
    busy.value = false
  }
}

onMounted(() => { loadPool(); loadMine() })
</script>
