<template>
  <div>
    <PageHeader title="Доливы" />

    <BaseTabs v-model="tab" :tabs="tabs">
      <!-- ── Мои доливы (как заказчик) ── -->
      <template #mine>
        <DataTable
          :columns="mineColumns"
          :rows="mine"
          :loading="loadingMine"
          row-key="id"
          clickable
          :show-pagination="false"
          empty-text="Доливов пока нет."
          @row-click="openInfo($event as Doliv)"
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
          <template #cell-exchange_rate="{ value }">
            <span class="text-text-main">{{ value != null ? formatAmount(Number(value)) : '—' }}</span>
          </template>
          <template #cell-price_usdt="{ value }">
            <span class="font-mono text-text-main">{{ value != null ? formatAmount(Number(value)) : '—' }}</span>
          </template>
          <template #cell-req_number="{ row }">
            <div v-if="(row as any).req_number" class="flex items-center gap-1">
              <PaymentOptionLogo :src="(row as any).logo_url ?? null" :alt="(row as any).req_extra ?? ''" :size="16" />
              <button
                class="group flex items-center rounded px-1 py-0.5 transition-colors hover:bg-bg-hover active:bg-bg-card"
                title="Скопировать"
                @click.stop="copyReq((row as any).req_number)"
              >
                <span class="font-mono text-xs text-text-main transition-colors group-hover:text-accent">{{ (row as any).req_number }}</span>
              </button>
            </div>
            <span v-else class="text-text-muted">—</span>
          </template>
          <template #cell-status="{ row }">
            <StatusBadge :status="(row as any).status" :expires-at="(row as any).claim_expires_at" context="doliv" />
          </template>
          <template #cell-created_at="{ value }">{{ formatDate(value) }}</template>
          <template #actions="{ row }">
            <BaseButton
              v-if="(row as any).status === 'created'"
              variant="dark"
              size="sm"
              :loading="mineBusy === (row as any).id"
              @click.stop="cancelMine(row as Doliv)"
            >
              Отменить
            </BaseButton>
          </template>
        </DataTable>
      </template>

      <!-- ── Пул (только доливщикам) ── -->
      <template v-if="isExecutor" #pool>
        <DataTable
          :columns="poolColumns"
          :rows="poolRows"
          :loading="loadingPool"
          row-key="id"
          clickable
          :show-pagination="false"
          empty-text="Доливов пока нет."
          @row-click="openInfo($event as Doliv)"
        >
          <template #cell-amount="{ row }">
            <span class="font-bold text-text-main">{{ formatAmount((row as any).amount) }}</span>
            <span class="ml-1 text-text-muted">{{ (row as any).currency }}</span>
          </template>
          <template #cell-bank="{ row }">
            <div class="flex items-center gap-1.5">
              <PaymentOptionLogo :src="(row as any).logo_url ?? null" :alt="(row as any).payment_option_name ?? ''" :size="16" />
              <span class="text-xs text-text-main">{{ (row as any).payment_option_name || (row as any).req_extra || '—' }}</span>
            </div>
          </template>
          <template #cell-payment_method="{ value }">
            <MethodBadge :method="value" />
          </template>
          <template #cell-exchange_rate="{ value }">
            <span class="text-text-main">{{ value != null ? formatAmount(Number(value)) : '—' }}</span>
          </template>
          <template #cell-req_number="{ row }">
            <button
              v-if="(row as any).req_number"
              class="group flex items-center rounded px-1 py-0.5 transition-colors hover:bg-bg-hover active:bg-bg-card"
              title="Скопировать"
              @click.stop="copyReq((row as any).req_number)"
            >
              <span class="font-mono text-xs text-text-main transition-colors group-hover:text-accent">{{ (row as any).req_number }}</span>
            </button>
            <span v-else class="font-mono text-text-muted">••••••</span>
          </template>
          <template #cell-executor_reward_usdt="{ value }">
            <span class="text-status-success">{{ value != null ? formatAmount(Number(value)) + ' USDT' : '—' }}</span>
          </template>
          <template #cell-status="{ row }">
            <StatusBadge :status="(row as any).status" :expires-at="(row as any).claim_expires_at" context="doliv" />
          </template>
          <template #cell-created_at="{ value }">{{ formatDate(value) }}</template>
          <template #actions="{ row }">
            <BaseButton
              v-if="(row as any).status === 'created'"
              variant="gold"
              size="sm"
              :loading="poolBusy === (row as any).id"
              @click.stop="claimDoliv(row as Doliv)"
            >
              Взять
            </BaseButton>
            <BaseButton
              v-else-if="(row as any).status === 'claimed'"
              variant="success"
              size="sm"
              :loading="poolBusy === (row as any).id"
              @click.stop="openExecute(row as Doliv)"
            >
              Завершить
            </BaseButton>
          </template>
        </DataTable>
      </template>
    </BaseTabs>

    <DolivInfoModal v-model="showInfo" :doliv="selected" />

    <!-- Завершить долив — прикрепить чек (обязательно) -->
    <BaseModal v-model="showExecute" title="Завершить долив">
      <div v-if="executeTarget" class="space-y-3 text-sm">
        <p class="text-text-muted">
          Подтвердите, что вы перевели
          <b class="text-text-main">{{ formatAmount(executeTarget.amount) }} {{ executeTarget.currency }}</b>
          на реквизит <code>{{ executeTarget.req_number || '—' }}</code>.
        </p>
        <input
          type="file"
          accept="image/*,application/pdf"
          class="text-sm"
          @change="onExecuteFile"
        />
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showExecute = false">Отмена</BaseButton>
        <BaseButton
          variant="success"
          :loading="poolBusy === executeTarget?.id"
          :disabled="!executeFile"
          @click="doExecute"
        >
          Завершить
        </BaseButton>
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
import BaseModal from '@/components/ui/BaseModal.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import PaymentOptionLogo from '@/components/ui/PaymentOptionLogo.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import DolivInfoModal from '@/components/doliv/DolivInfoModal.vue'
import { dolivService } from '@/api/services/doliv.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { formatAmount, formatDate, copyToClipboard } from '@/utils/format'
import type { Doliv } from '@/types'

const toast = useToast()
const { confirm: askConfirm } = useConfirm()

const tab = ref('mine')

// ── Мои доливы (заказчик) ──
const loadingMine = ref(false)
const mineBusy = ref<string | null>(null)
const mine = ref<Doliv[]>([])
const showInfo = ref(false)
const selected = ref<Doliv | null>(null)

// ── Пул (доливщик) ──
const isExecutor = ref(false)
const loadingPool = ref(false)
const poolBusy = ref<string | null>(null)
const poolRows = ref<Doliv[]>([])

// Исполнить = прикрепить чек + settle.
const showExecute = ref(false)
const executeTarget = ref<Doliv | null>(null)
const executeFile = ref<File | null>(null)

const tabs = computed<Tab[]>(() => {
  const t: Tab[] = [{ key: 'mine', label: 'Мои доливы', count: mine.value.length }]
  if (isExecutor.value) {
    t.push({ key: 'pool', label: 'Пул', count: poolRows.value.filter((d) => d.status === 'created').length })
  }
  return t
})

const mineColumns: Column[] = [
  { key: 'id', label: 'UUID' },
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'amount_usdt', label: 'USDT', align: 'right' },
  { key: 'exchange_rate', label: 'Курс', align: 'right' },
  { key: 'price_usdt', label: 'Цена', align: 'right' },
  { key: 'req_number', label: 'Реквизит' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Создан' },
]

const poolColumns: Column[] = [
  { key: 'amount', label: 'Сумма', align: 'right' },
  { key: 'bank', label: 'Банк' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'req_number', label: 'Реквизит' },
  { key: 'exchange_rate', label: 'Курс', align: 'right' },
  { key: 'executor_reward_usdt', label: 'Награда', align: 'right' },
  { key: 'status', label: 'Статус' },
  { key: 'created_at', label: 'Создан' },
]

function openInfo(d: Doliv) {
  selected.value = d
  showInfo.value = true
}

async function copyReq(text: string) {
  try { await copyToClipboard(text); toast.success('Скопировано') }
  catch { toast.error('Не удалось скопировать') }
}

async function cancelMine(d: Doliv) {
  const ok = await askConfirm({
    title: 'Отменить долив?',
    message: 'Замороженные средства вернутся на ваш баланс.',
    confirmText: 'Отменить',
    cancelText: 'Назад',
    variant: 'danger',
  })
  if (!ok) return
  mineBusy.value = d.id
  try {
    await dolivService.cancel(d.id)
    toast.success('Долив отменён')
    await loadMine()
  } catch (e: any) {
    toast.error(e.response?.data?.detail || e.response?.data?.message || 'Не удалось отменить')
  } finally { mineBusy.value = null }
}

async function loadMine() {
  loadingMine.value = true
  try { mine.value = (await dolivService.mine({ limit: 100 })).data }
  catch { toast.error('Ошибка загрузки доливов') }
  finally { loadingMine.value = false }
}

async function loadPool() {
  loadingPool.value = true
  try { poolRows.value = (await dolivService.executor({ limit: 100 })).data }
  catch { toast.error('Ошибка загрузки доливов') }
  finally { loadingPool.value = false }
}

async function loadAccess() {
  try {
    const { data } = await dolivService.access()
    isExecutor.value = data.is_executor
    if (data.is_executor) await loadPool()
  } catch { /* non-executor / error → пул скрыт */ }
}

async function claimDoliv(d: Doliv) {
  poolBusy.value = d.id
  try {
    await dolivService.claim(d.id)
    toast.success('Долив взят')
    await loadPool()
  } catch (e: any) {
    toast.error(e.response?.data?.detail || e.response?.data?.message || 'Не удалось взять')
  } finally { poolBusy.value = null }
}

function openExecute(d: Doliv) {
  executeTarget.value = d
  executeFile.value = null
  showExecute.value = true
}

function onExecuteFile(e: Event) {
  executeFile.value = (e.target as HTMLInputElement).files?.[0] ?? null
}

async function doExecute() {
  const d = executeTarget.value
  if (!d || !executeFile.value) { toast.error('Прикрепите чек'); return }
  poolBusy.value = d.id
  try {
    await dolivService.execute(d.id, executeFile.value)
    toast.success('Долив завершён')
    showExecute.value = false
    await loadPool()
  } catch (e: any) {
    toast.error(e.response?.data?.detail || e.response?.data?.message || 'Не удалось исполнить')
  } finally { poolBusy.value = null }
}

onMounted(() => { loadMine(); loadAccess() })
</script>
