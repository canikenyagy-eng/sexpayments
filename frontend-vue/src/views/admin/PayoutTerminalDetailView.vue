<template>
  <div class="pb-24">
    <div v-if="loading && !editing" class="flex min-h-[200px] items-center justify-center">
      <span class="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
      <span class="ml-3 text-sm text-text-muted">Загрузка...</span>
    </div>

    <div v-else-if="!editing" class="rounded-xl border border-border bg-bg-card p-6 text-center text-text-muted">
      Терминал не найден.
    </div>

    <template v-else>
      <header class="mb-4">
        <section class="space-y-2 rounded-2xl border p-4 shadow-prime transition-colors" :class="cardToneClass">
          <div class="flex items-center justify-between gap-2">
            <StatusBadge :status="editing.status" context="merchant" />
            <BaseMenu>
              <BaseMenuItem :icon="Wallet" @click="showTopup = true">Пополнить баланс</BaseMenuItem>
              <BaseMenuItem :icon="KeyRound" @click="showKey = true">Показать ключ интеграции</BaseMenuItem>
            </BaseMenu>
          </div>
          <h1 class="truncate text-xl font-bold text-text-main">{{ editing.name || `#${editing.id}` }}</h1>
          <div class="text-xs text-text-muted">ID <span class="font-mono tabular-nums text-text-main">{{ editing.id }}</span></div>
        </section>
      </header>

      <!-- Балансы -->
      <section class="mb-4">
        <div class="grid grid-cols-2 gap-3 sm:grid-cols-3">
          <StatCard label="WORK USDT" :value="formatAmount(editing.work_usdt)" :icon="Wallet" />
          <StatCard label="ESCROW USDT" :value="formatAmount(editing.escrow_usdt)" :icon="Lock" />
          <StatCard label="Трейдеров" :value="editForm.trader_ids.length" :icon="Users" />
        </div>
      </section>

      <div>
        <BaseTabs v-model="activeTab" :tabs="tabs">
          <template #general>
            <div class="space-y-6 pt-2">
              <FormSection label="Идентификация">
                <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <BaseInput v-model="editForm.name" label="Название" />
                  <BaseSelect v-model="editForm.status" label="Статус" :options="statusOptions" />
                  <BaseSelect v-model="editForm.currency" label="Валюта" :options="currencyOptions" />
                  <BaseSelect v-model="editForm.rate_config_id" label="Конфиг курса" :options="rateConfigOptions" />
                  <div class="sm:col-span-2">
                    <BaseInput v-model="editForm.webhook_url" label="Webhook URL" placeholder="https://…" />
                  </div>
                </div>
              </FormSection>

              <FormSection label="Экономика выплат">
                <div class="grid grid-cols-2 gap-4">
                  <BaseInput v-model="editForm.commission_percent" label="Комиссия % (цена мерчанта)" type="number" />
                  <BaseInput v-model="editForm.ttl_minutes" label="TTL выплаты (минут)" type="number" />
                  <BaseInput v-model="editForm.receipts_to_close" label="Чеков для закрытия (N)" type="number" />
                  <div />
                  <BaseInput v-model="editForm.min_amount" label="Мин. сумма (фиат)" type="number" />
                  <BaseInput v-model="editForm.max_amount" label="Макс. сумма (фиат)" type="number" />
                </div>
              </FormSection>
            </div>
          </template>

          <template #access>
            <div class="space-y-4 pt-2">
              <div class="rounded-lg border border-border bg-bg-card px-3 py-2 text-xs text-text-muted">
                Выплаты этого терминала видят в пуле только привязанные трейдеры. Пусто — никто не видит.
              </div>
              <BaseTransferList
                v-model="editForm.trader_ids"
                :options="traderOptions"
                label="Трейдеры"
                available-label="Доступные"
                selected-label="Привязанные"
                search-placeholder="Поиск по логину или ID"
              />
            </div>
          </template>
        </BaseTabs>

        <div class="fixed bottom-0 left-0 right-0 z-30 border-t border-border bg-bg-surface/95 backdrop-blur supports-[backdrop-filter]:bg-bg-surface/80 lg:left-64">
          <div class="mx-auto flex max-w-6xl items-center justify-end gap-3 px-4 py-3 sm:px-6">
            <span v-if="isDirty" class="mr-auto text-sm text-text-muted">Несохранённые изменения</span>
            <BaseButton variant="dark" @click="goBack">Отмена</BaseButton>
            <BaseButton variant="gold" :loading="saving" :disabled="!isDirty" @click="saveEdit">Сохранить</BaseButton>
          </div>
        </div>
      </div>
    </template>

    <!-- Topup -->
    <BaseModal v-model="showTopup" title="Пополнить баланс терминала">
      <div class="space-y-3">
        <BaseInput v-model="topupAmount" label="Сумма USDT" type="number" />
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showTopup = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="saving" @click="doTopup">Пополнить</BaseButton>
      </template>
    </BaseModal>

    <!-- API key -->
    <BaseModal v-model="showKey" title="Ключ интеграции терминала">
      <div class="space-y-2">
        <p class="text-xs text-text-muted">Секрет показывается только при создании.</p>
        <div class="rounded-xl bg-bg-card p-3 font-mono text-sm text-accent break-all">{{ editing?.api_key }}</div>
      </div>
      <template #footer><BaseButton variant="dark" @click="showKey = false">Закрыть</BaseButton></template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseMenu from '@/components/ui/BaseMenu.vue'
import BaseMenuItem from '@/components/ui/BaseMenuItem.vue'
import BaseTabs from '@/components/ui/BaseTabs.vue'
import BaseTransferList from '@/components/ui/BaseTransferList.vue'
import StatCard from '@/components/ui/StatCard.vue'
import FormSection from '@/components/ui/FormSection.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import { KeyRound, Lock, Users, Wallet } from 'lucide-vue-next'
import { payoutsService } from '@/api/services/payouts.service'
import { usersService } from '@/api/services/users.service'
import { tradersService } from '@/api/services/traders.service'
import { ratesService } from '@/api/services/rates.service'
import { useToast } from '@/composables/useToast'
import { formatAmount } from '@/utils/format'
import { merchantStatusOptions } from '@/constants'
import type { PayoutTerminal, RateConfig, Trader, User } from '@/types'

const route = useRoute()
const router = useRouter()
const toast = useToast()

const routeId = computed(() => Number(route.params.id))

const loading = ref(false)
const saving = ref(false)
const editing = ref<PayoutTerminal | null>(null)

const activeTab = ref('general')
const tabs = [
  { key: 'general', label: 'Основные' },
  { key: 'access', label: 'Трейдеры' },
]

const statusOptions = merchantStatusOptions
const currencyOptions = [
  { value: 'RUB', label: 'RUB' }, { value: 'AZN', label: 'AZN' }, { value: 'USDT', label: 'USDT' },
]

const editForm = reactive({
  name: '', status: '', currency: 'RUB', rate_config_id: '' as string,
  commission_percent: '0', ttl_minutes: '60', receipts_to_close: '1',
  min_amount: '', max_amount: '', webhook_url: '',
  trader_ids: [] as number[],
})

const showTopup = ref(false)
const topupAmount = ref('')
const showKey = ref(false)

const rateConfigs = ref<RateConfig[]>([])
const rateConfigOptions = computed(() => [
  { value: '', label: 'Авто (по валюте)' },
  ...rateConfigs.value.map(r => ({ value: String(r.id), label: `#${r.id} ${r.name} (${r.fiat_currency}, ${r.current_rate ?? '—'})` })),
])

const allTraders = ref<Trader[]>([])
const allTraderUsers = ref<Record<number, User>>({})
const traderOptions = computed(() =>
  allTraders.value.map(t => ({
    value: t.user_id,
    label: allTraderUsers.value[t.user_id]?.username || `Trader #${t.id}`,
    sublabel: `#${t.user_id}`,
  }))
)

const cardToneClass = computed(() => {
  switch (editing.value?.status) {
    case 'enabled': return 'border-status-success/40 bg-status-success/10'
    case 'blocked': return 'border-status-danger/40 bg-status-danger/10'
    case 'pending': return 'border-status-warning/40 bg-status-warning/10'
    case 'test': return 'border-status-info/40 bg-status-info/10'
    default: return 'border-border bg-bg-card'
  }
})

const initialSnapshot = ref('')
const isDirty = computed(() => initialSnapshot.value !== JSON.stringify(editForm))

function fillForm(t: PayoutTerminal) {
  editForm.name = t.name || ''
  editForm.status = t.status
  editForm.currency = t.currency || 'RUB'
  editForm.rate_config_id = t.rate_config_id != null ? String(t.rate_config_id) : ''
  editForm.commission_percent = String(t.commission_percent ?? 0)
  editForm.ttl_minutes = String(t.ttl_minutes ?? 60)
  editForm.receipts_to_close = String(t.receipts_to_close ?? 1)
  editForm.min_amount = t.min_amount != null ? String(t.min_amount) : ''
  editForm.max_amount = t.max_amount != null ? String(t.max_amount) : ''
  editForm.webhook_url = t.webhook_url || ''
  editForm.trader_ids = [...(t.trader_ids || [])]
  initialSnapshot.value = JSON.stringify(editForm)
}

async function loadTerminal() {
  if (!Number.isFinite(routeId.value)) return
  loading.value = true
  try {
    const { data } = await payoutsService.getTerminal(routeId.value)
    editing.value = data
    fillForm(data)
  } catch { toast.error('Не удалось загрузить терминал'); editing.value = null }
  finally { loading.value = false }
}

async function loadOptions() {
  try {
    const [tradersRes, usersRes, ratesRes] = await Promise.all([
      tradersService.list({ limit: 1000 }),
      usersService.list({ role: 'trader', limit: 1000 }),
      ratesService.list(),
    ])
    allTraders.value = tradersRes.data
    rateConfigs.value = ratesRes.data
    const map: Record<number, User> = {}
    for (const u of usersRes.data) map[u.id] = u
    allTraderUsers.value = map
  } catch { /* soft */ }
}

async function saveEdit() {
  if (!editing.value) return
  saving.value = true
  try {
    await payoutsService.updateTerminal(editing.value.id, {
      name: editForm.name,
      status: editForm.status,
      currency: editForm.currency,
      rate_config_id: editForm.rate_config_id ? Number(editForm.rate_config_id) : null,
      commission_percent: Number(editForm.commission_percent),
      ttl_minutes: Number(editForm.ttl_minutes),
      receipts_to_close: Number(editForm.receipts_to_close),
      min_amount: editForm.min_amount ? Number(editForm.min_amount) : null,
      max_amount: editForm.max_amount ? Number(editForm.max_amount) : null,
      webhook_url: editForm.webhook_url || null,
      trader_ids: editForm.trader_ids,
    })
    toast.success('Терминал обновлён')
    await loadTerminal()
  } catch (e: any) { toast.error(e.response?.data?.detail || 'Ошибка') }
  finally { saving.value = false }
}

async function doTopup() {
  if (!editing.value) return
  saving.value = true
  try {
    await payoutsService.topupTerminal(editing.value.id, Number(topupAmount.value))
    toast.success('Баланс пополнен')
    showTopup.value = false
    topupAmount.value = ''
    await loadTerminal()
  } catch (e: any) { toast.error(e.response?.data?.detail || 'Ошибка пополнения') }
  finally { saving.value = false }
}

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/admin/payout-terminals')
}

watch(routeId, (id) => { if (Number.isFinite(id)) loadTerminal() })

onMounted(() => { loadTerminal(); loadOptions() })
</script>
