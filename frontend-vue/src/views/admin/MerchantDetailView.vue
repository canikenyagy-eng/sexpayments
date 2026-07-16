<template>
  <div class="pb-24">
    <div v-if="loading && !editing" class="flex min-h-[200px] items-center justify-center">
      <span class="h-6 w-6 animate-spin rounded-full border-2 border-accent border-t-transparent" />
      <span class="ml-3 text-sm text-text-muted">Загрузка...</span>
    </div>

    <div v-else-if="!editing" class="rounded-xl border border-border bg-bg-card p-6 text-center text-text-muted">
      Мерчант не найден.
    </div>

    <template v-else>
      <header class="mb-4">
        <section
          class="space-y-2 rounded-2xl border p-4 shadow-prime transition-colors"
          :class="cardToneClass"
        >
          <div class="flex items-center justify-between gap-2">
            <StatusBadge :status="editing.status" context="merchant" />
            <BaseMenu>
              <BaseMenuItem :icon="Wallet" @click="openEditBalance">Корректировка баланса</BaseMenuItem>
              <BaseMenuItem :icon="FileText" @click="goToApiLogs">API логи мерчанта</BaseMenuItem>
              <BaseMenuItem :icon="RotateCw" dangerous @click="resetKeyForEditing">Сбросить API ключ</BaseMenuItem>
            </BaseMenu>
          </div>

          <h1 class="truncate text-xl font-bold text-text-main">
            {{ editing.name || `#${editing.id}` }}
          </h1>

          <div class="text-xs text-text-muted">
            ID <span class="font-mono tabular-nums text-text-main">{{ editing.id }}</span>
          </div>
        </section>
      </header>

      <!-- ── Метрики мерчанта ────────────── -->
      <section class="mb-4">
        <div class="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 class="hidden text-sm font-bold uppercase tracking-wider text-text-muted sm:block">Статистика</h2>
          <div class="grid w-full grid-cols-2 gap-2 sm:flex sm:w-auto sm:items-center">
            <BaseDatePicker
              v-model="statsFrom"
              with-time
              placeholder="От"
              button-class="w-full sm:w-[170px]"
              @change="loadStats"
            />
            <BaseDatePicker
              v-model="statsTo"
              with-time
              placeholder="До"
              button-class="w-full sm:w-[170px]"
              @change="loadStats"
            />
          </div>
        </div>
        <div class="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
          <StatCard
            v-for="card in statCards"
            :key="card.label"
            :label="card.label"
            :value="card.value"
            :icon="card.icon"
            :loading="loadingStats"
          />
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
                <div class="sm:col-span-2">
                  <BaseInput v-model="editForm.webhook_url" label="Webhook URL" placeholder="https://…" />
                </div>
              </div>
            </FormSection>

            <FormSection label="Ордера и расчёты">
              <div class="grid grid-cols-2 gap-4">
                <BaseInput v-model="editForm.order_ttl_seconds" label="TTL ордера (сек)" type="number" />
                <BaseInput v-model="editForm.requisite_search_timeout_ms" label="Таймаут поиска (мс)" type="number" />
              </div>
              <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <BaseInput v-model="editForm.withdrawal_fee_fixed" label="Комиссия вывода (фикс)" type="number" />
                <BaseSelect v-model="editForm.rate_config_id" label="Конфиг курса" :options="rateConfigOptions" />
              </div>
            </FormSection>

            <FormSection
              label="Премодерация чеков"
              help="Выключено — используется глобальная настройка платформы. Включено — задаёт принудительное переопределение для этого мерчанта."
            >
              <div class="space-y-3 rounded-xl border border-border bg-bg-card p-4">
                <div class="flex items-center justify-between gap-3">
                  <div class="min-w-0">
                    <div class="text-sm font-bold text-text-main">Премодерация чеков</div>
                    <div class="text-xs text-text-muted">Чеки проверяются перед зачислением</div>
                  </div>
                  <BaseSwitch v-model="premoderationOverride" />
                </div>
                <div v-if="premoderationOverride">
                  <BaseSelect
                    v-model="editForm.premoderation_mode"
                    label="Режим премодерации"
                    :options="premoderationForceOptions"
                  />
                </div>
              </div>
            </FormSection>

            <FormSection
              label="Уникальные клиенты"
              help="Включать для мерчантов, которые в userId передают уникальный айди клиента"
            >
              <div class="flex items-center justify-between gap-3 rounded-xl border border-border bg-bg-card p-4">
                <div class="min-w-0">
                  <div class="text-sm font-bold text-text-main">Уникальные клиенты</div>
                </div>
                <BaseSwitch v-model="editForm.unique_clients_enabled" />
              </div>
            </FormSection>

            <FormSection
              label="Запрос PDF/видео в чат"
              help="Включено (по умолчанию) — при запросе PDF/видео по ордеру мерчант получает уведомление в чат (merchant-notify-bot; для ордеров из бота — merchant-bot с кнопкой «приложить»). Выключено — запрос идёт только по API (в вебхуке dispute.substatus)."
            >
              <div class="flex items-center justify-between gap-3 rounded-xl border border-border bg-bg-card p-4">
                <div class="min-w-0">
                  <div class="text-sm font-bold text-text-main">Запрос PDF/видео в чат</div>
                </div>
                <BaseSwitch v-model="editForm.proof_request_notify_enabled" />
              </div>
            </FormSection>

            <FormSection
              label="Telegram уведомления"
              help="Notify group — куда merchant-notify-bot шлёт запросы PDF/Видео при премодерации. Dispute group — чат, откуда merchant-dispute-bot принимает чеки (uuid/external_id + файл) и отправляет их в премодерацию. Маска ID — позиция нашего идентификатора в тексте апелляции: «N» или «word:N» — N-е слово (с 1), «uuid:N» — N-й uuid. Пусто — берём первый токен, который найдётся в нашей базе."
            >
              <BaseInput
                v-model="editForm.notify_telegram_group_id"
                label="Telegram group ID (merchant-notify-bot)"
                type="number"
                placeholder="-1001234567890"
              />
              <BaseInput
                v-model="editForm.dispute_telegram_group_id"
                label="Telegram group ID (merchant-dispute-bot — приём чеков)"
                type="number"
                placeholder="-1001234567890"
              />
              <div class="space-y-1.5">
                <div class="flex items-center gap-1.5">
                  <span class="text-sm font-semibold text-text-secondary">Маска ID апелляции</span>
                  <BaseHelp
                    text="Откуда в тексте апелляции взять НАШ ID заказа (uuid или external_id). Мерчанты шлют его в разном формате — часто после своего внутреннего ID. Форматы: «N» или «word:N» — N-е слово (счёт с 1); «uuid:N» — N-й uuid в тексте. Примеры: наш uuid идёт вторым → «uuid:2»; наш ID это 4-е слово → «4». Пусто — берём первый токен, который найдётся в нашей базе. Ниже можно проверить маску на примере текста."
                  />
                </div>
                <BaseInput
                  v-model="editForm.dispute_id_mask"
                  placeholder="напр. uuid:2 или 4"
                />
              </div>
              <DisputeMaskTester :mask="editForm.dispute_id_mask" />
            </FormSection>
          </div>
        </template>

        <template #fees>
          <div class="space-y-6 pt-2">
            <FormSection label="Комиссии по методам (%)">
              <template #actions>
                <BaseButton variant="ghost" size="sm" @click="addFee">+ Метод</BaseButton>
              </template>
              <div v-for="(item, idx) in editFees" :key="idx" class="flex items-center gap-2">
                <BaseSelect v-model="item.method" :options="feeMethodOptions" class="w-32" />
                <BaseInput v-model="item.fee" label="" placeholder="%" type="number" class="flex-1" />
                <button
                  type="button"
                  class="text-xs text-status-danger hover:underline"
                  @click="editFees.splice(idx, 1)"
                >×</button>
              </div>
              <p v-if="!editFees.length" class="text-sm text-text-muted">
                Нет комиссий. Добавьте метод, чтобы задать процент.
              </p>
            </FormSection>

            <FormSection label="Telegram Bot — разрешённые пользователи">
              <template #actions>
                <BaseButton variant="ghost" size="sm" @click="addTgId">+ Добавить</BaseButton>
              </template>
              <div class="flex flex-col gap-2">
                <div
                  v-for="tg in editTgIds"
                  :key="tg.id"
                  class="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-bg-card px-3 py-2"
                >
                  <span class="font-mono text-xs text-accent">{{ tg.id }}</span>
                  <BaseInput
                    v-model="tg.label"
                    placeholder="Название (например: сотрудник)"
                    class="flex-1 min-w-[200px]"
                  />
                  <button
                    type="button"
                    class="text-text-muted hover:text-status-danger"
                    @click="removeTgId(tg.id)"
                  >×</button>
                </div>
                <p v-if="!editTgIds.length" class="text-sm text-text-muted">
                  Нет доступных пользователей.
                </p>
              </div>
              <div class="flex flex-col gap-2 sm:flex-row">
                <BaseInput v-model="newTgIdInput" placeholder="Telegram User ID" type="number" class="flex-1" />
                <BaseInput v-model="newTgLabelInput" placeholder="Название (необязательно)" class="flex-1" />
                <BaseButton variant="dark" size="sm" @click="addTgId">Добавить</BaseButton>
              </div>
            </FormSection>
          </div>
        </template>

        <template #access>
          <div class="space-y-4 pt-2">
            <div class="rounded-lg border border-border bg-bg-card px-3 py-2 text-xs text-text-muted">
              Если оба списка пусты — ордера получают любые трейдеры.
            </div>

            <BaseTransferList
              v-model="editForm.trader_ids"
              :options="traderOptions"
              label="Трейдеры"
              available-label="Доступные"
              selected-label="Привязанные"
              search-placeholder="Поиск по логину или ID"
            />

            <BaseTransferList
              v-model="editForm.group_ids"
              :options="groupOptions"
              label="Группы"
              available-label="Доступные"
              selected-label="Привязанные"
              search-placeholder="Поиск по группам"
            />
          </div>
        </template>

        <template #cascade>
          <div class="space-y-6 pt-2">
            <FormSection
              label="Режим каскада"
              help="off — каскад не используется. grouped — провайдеры каскада дёргаются только когда локальный pool не нашёл реквизит, и только из привязанных групп. pooled — то же, но провайдеры обходятся по общему скору, минуя группы."
            >
              <BaseSelect
                v-model="editForm.cascade_mode"
                label="Режим каскада"
                :options="cascadeModeOptions"
              />
            </FormSection>

            <FormSection
              label="Группы каскада"
              help="Используются только при режиме grouped. Каскад обойдёт провайдеров привязанных к этим группам в порядке их tier'а."
            >
              <BaseTransferList
                v-model="editForm.cascade_group_ids"
                :options="cascadeGroupOptions"
                available-label="Доступные"
                selected-label="Привязанные"
                search-placeholder="Поиск по группам каскада"
              />
            </FormSection>
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

    <!-- API key display (after reset) -->
    <BaseModal v-model="showApiKey" title="Новый API ключ">
      <div class="space-y-3">
        <p class="text-sm text-status-warning">Сохраните ключ и секрет — они больше не будут показаны!</p>
        <div>
          <p class="mb-1 text-xs font-bold text-text-muted">API Key</p>
          <div class="rounded-xl bg-bg-card p-3 font-mono text-sm text-accent break-all">{{ newApiKey }}</div>
        </div>
        <div>
          <p class="mb-1 text-xs font-bold text-text-muted">API Secret</p>
          <div class="rounded-xl bg-bg-card p-3 font-mono text-sm text-accent break-all">{{ newApiSecret }}</div>
        </div>
      </div>
      <template #footer>
        <BaseButton variant="gold" @click="showApiKey = false">Закрыть</BaseButton>
      </template>
    </BaseModal>

    <BalanceAdjustModal
      :open="showBalanceModal"
      :initial-merchant-id="balanceInitialMerchantId"
      :allowed-types="[{ value: 'merchant', label: 'Мерчант' }]"
      :lock-entity-type="!!balanceInitialMerchantId"
      @update:open="showBalanceModal = $event"
      @success="loadMerchant"
    />
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import DisputeMaskTester from '@/components/merchants/DisputeMaskTester.vue'
import BaseHelp from '@/components/ui/BaseHelp.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseMenu from '@/components/ui/BaseMenu.vue'
import BaseMenuItem from '@/components/ui/BaseMenuItem.vue'
import BaseSwitch from '@/components/ui/BaseSwitch.vue'
import BaseTabs from '@/components/ui/BaseTabs.vue'
import BaseTransferList from '@/components/ui/BaseTransferList.vue'
import BaseDatePicker from '@/components/ui/BaseDatePicker.vue'
import StatCard from '@/components/ui/StatCard.vue'
import FormSection from '@/components/ui/FormSection.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import BalanceAdjustModal from '@/components/modals/BalanceAdjustModal.vue'
import {
  FileText, RotateCw, Wallet,
  TrendingUp, BarChart3, CreditCard, Package, CheckCircle2, Target, AlertTriangle,
} from 'lucide-vue-next'
import { cascadeService } from '@/api/services/cascade.service'
import { merchantsService } from '@/api/services/merchants.service'
import { usersService } from '@/api/services/users.service'
import { tradersService } from '@/api/services/traders.service'
import { ratesService } from '@/api/services/rates.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { formatInt } from '@/utils/format'
import { toUnixTs } from '@/utils/datetime'
import {
  merchantStatusOptions,
  paymentMethodOptions,
} from '@/constants'
import type {
  CascadeGroup, CascadeMode, MerchantAdmin, MerchantStats, RateConfig, Trader, TraderGroup, User,
} from '@/types'

const route = useRoute()
const router = useRouter()
const toast = useToast()
const { confirm } = useConfirm()

const routeId = computed(() => Number(route.params.id))

const loading = ref(false)
const saving = ref(false)
const editing = ref<MerchantAdmin | null>(null)

// ── Stat cards (как на дашборде, но scoped к мерчанту) ──────
const stats = ref<MerchantStats | null>(null)
const loadingStats = ref(false)
const statsFrom = ref('')
const statsTo = ref('')

const statCards = computed(() => {
  const s = stats.value
  return [
    { label: 'Оборот USDT', value: formatInt(s?.turnover_usdt ?? 0), icon: Wallet },
    { label: 'Прибыль USDT', value: formatInt(s?.fee_usdt ?? 0), icon: TrendingUp },
    { label: 'Запросов RUB', value: formatInt(s?.requests_rub ?? 0), icon: BarChart3 },
    { label: 'Выдача %', value: Math.round(s?.payout_pct ?? 0) + '%', icon: CreditCard },
    { label: 'Сделок всего', value: s?.orders_total ?? 0, icon: Package },
    { label: 'Успешных', value: s?.orders_success ?? 0, icon: CheckCircle2 },
    { label: 'Конверсия %', value: Math.round(s?.conversion_pct ?? 0) + '%', icon: Target },
    { label: 'Споры', value: s?.active_disputes ?? 0, icon: AlertTriangle },
  ]
})

async function loadStats() {
  if (!Number.isFinite(routeId.value)) return
  loadingStats.value = true
  try {
    const params: Record<string, number> = {}
    if (statsFrom.value) params.date_from = toUnixTs(statsFrom.value)
    if (statsTo.value) params.date_to = toUnixTs(statsTo.value, { endOfDay: true })
    const { data } = await merchantsService.getStatsAdmin(routeId.value, params)
    stats.value = data
  } catch {
    // карточки покажут 0 — не критично, не спамим тостами
  } finally {
    loadingStats.value = false
  }
}

const editForm = reactive({
  name: '', status: '', webhook_url: '', order_ttl_seconds: '',
  requisite_search_timeout_ms: '', withdrawal_fee_fixed: '',
  rate_config_id: '' as string,
  trader_ids: [] as number[],
  group_ids: [] as number[],
  premoderation_mode: '' as '' | 'true' | 'false',
  notify_telegram_group_id: '' as string,
  dispute_telegram_group_id: '' as string,
  dispute_id_mask: '' as string,
  cascade_mode: 'off' as CascadeMode,
  cascade_group_ids: [] as number[],
  unique_clients_enabled: false,
  proof_request_notify_enabled: true,
})

interface FeeEdit { method: string; fee: string }
interface TgEdit { id: number; label: string }
const editFees = ref<FeeEdit[]>([])
const editTgIds = ref<TgEdit[]>([])
const newTgIdInput = ref('')
const newTgLabelInput = ref('')

const showApiKey = ref(false)
const newApiKey = ref('')
const newApiSecret = ref('')

const showBalanceModal = ref(false)
const balanceInitialMerchantId = ref<number | null>(null)

const activeTab = ref('general')
const tabs = [
  { key: 'general', label: 'Основные' },
  { key: 'fees', label: 'Комиссии и Telegram' },
  { key: 'access', label: 'Трейдеры и Группы' },
  { key: 'cascade', label: 'Каскад' },
]

const premoderationForceOptions = [
  { value: 'true', label: 'Принудительно включена' },
  { value: 'false', label: 'Принудительно выключена' },
]

const premoderationOverride = computed<boolean>({
  get: () => editForm.premoderation_mode !== '',
  set: (on) => { editForm.premoderation_mode = on ? 'true' : '' },
})

const cascadeModeOptions = [
  { value: 'off', label: 'Выключен (off)' },
  { value: 'grouped', label: 'По группам (grouped)' },
  { value: 'pooled', label: 'По общему пулу (pooled)' },
]

const statusOptions = merchantStatusOptions
const feeMethodOptions = paymentMethodOptions

const rateConfigs = ref<RateConfig[]>([])
const rateConfigOptions = computed(() => [
  { value: '', label: 'Авто (по валюте)' },
  ...rateConfigs.value.map(r => ({
    value: String(r.id),
    label: `#${r.id} ${r.name} (${r.fiat_currency}, ${r.current_rate ?? '—'})`,
  })),
])

const allTraders = ref<Trader[]>([])
const allGroups = ref<TraderGroup[]>([])
const allTraderUsers = ref<Record<number, User>>({})
const allCascadeGroups = ref<CascadeGroup[]>([])

const traderOptions = computed(() =>
  allTraders.value.map(t => {
    const u = allTraderUsers.value[t.user_id]
    return {
      value: t.id,
      label: u?.username || `Trader #${t.id}`,
      sublabel: `#${t.id}`,
    }
  }),
)

const groupOptions = computed(() =>
  allGroups.value.map(g => ({
    value: g.id,
    label: g.name,
    sublabel: g.description || undefined,
  })),
)

const cascadeGroupOptions = computed(() =>
  allCascadeGroups.value.map(g => ({
    value: g.id,
    label: g.name,
    sublabel: g.is_active === false ? `#${g.id} · неактивна` : `#${g.id} · tier ${g.tier}`,
  })),
)

const cardToneClass = computed(() => {
  switch (editing.value?.status) {
    case 'enabled':
      return 'border-status-success/40 bg-status-success/10'
    case 'blocked':
      return 'border-status-danger/40 bg-status-danger/10'
    case 'pending':
      return 'border-status-warning/40 bg-status-warning/10'
    case 'test':
      return 'border-status-info/40 bg-status-info/10'
    case 'disabled':
    case 'archived':
    default:
      return 'border-border bg-bg-card'
  }
})

const initialFormSnapshot = ref<string>('')
const isDirty = computed(() => initialFormSnapshot.value !== currentFormSnapshot())

function currentFormSnapshot(): string {
  return JSON.stringify({ form: editForm, fees: editFees.value, tgIds: editTgIds.value })
}

function fillFormFromMerchant(m: MerchantAdmin) {
  editForm.name = m.name || ''
  editForm.status = m.status
  editForm.webhook_url = m.webhook_url || ''
  editForm.order_ttl_seconds = String(m.order_ttl_seconds ?? 900)
  editForm.requisite_search_timeout_ms = String(m.requisite_search_timeout_ms ?? 300)
  editForm.withdrawal_fee_fixed = String(m.withdrawal_fee_fixed ?? 0)
  editForm.rate_config_id = m.rate_config_id ? String(m.rate_config_id) : ''
  editForm.trader_ids = (m.traders || []).map(t => t.id)
  editForm.group_ids = (m.trader_groups || []).map(g => g.id)
  editForm.premoderation_mode =
    m.receipt_premoderation_enabled === true
      ? 'true'
      : m.receipt_premoderation_enabled === false
        ? 'false'
        : ''
  editForm.notify_telegram_group_id =
    m.notify_telegram_group_id != null ? String(m.notify_telegram_group_id) : ''
  editForm.dispute_telegram_group_id =
    m.dispute_telegram_group_id != null ? String(m.dispute_telegram_group_id) : ''
  editForm.dispute_id_mask = m.dispute_id_mask ?? ''
  editForm.cascade_mode = (m.cascade_mode as CascadeMode) ?? 'off'
  editForm.cascade_group_ids = [...(m.cascade_group_ids ?? [])]
  editForm.unique_clients_enabled = m.unique_clients_enabled ?? false
  editForm.proof_request_notify_enabled = m.proof_request_notify_enabled ?? true

  const fees = m.fees || {}
  editFees.value = Object.entries(fees).map(([method, fee]) => ({ method, fee: String(fee) }))
  editTgIds.value = (m.telegram_user_ids ?? []).map((t: any) =>
    typeof t === 'number' ? { id: t, label: '' } : { id: t.id, label: t.label ?? '' },
  )
  newTgIdInput.value = ''
  newTgLabelInput.value = ''
}

async function loadMerchant() {
  if (!Number.isFinite(routeId.value)) return
  loading.value = true
  try {
    const { data } = await merchantsService.getByIdAdmin(routeId.value)
    editing.value = data
    fillFormFromMerchant(data)
    initialFormSnapshot.value = currentFormSnapshot()
    loadStats()  // карточки грузим параллельно, не блокируя профиль
  } catch {
    toast.error('Не удалось загрузить мерчанта')
    editing.value = null
  } finally {
    loading.value = false
  }
}

async function loadOptions() {
  try {
    const [tradersRes, tGroupsRes, usersRes, ratesRes, cascadeGroupsRes] = await Promise.all([
      tradersService.list({ limit: 1000 }),
      tradersService.listGroups(),
      usersService.list({ role: 'trader', limit: 1000 }),
      ratesService.list(),
      cascadeService.listGroups(),
    ])
    allTraders.value = tradersRes.data
    allGroups.value = tGroupsRes.data
    rateConfigs.value = ratesRes.data
    allCascadeGroups.value = cascadeGroupsRes.data
    const map: Record<number, User> = {}
    for (const u of usersRes.data) map[u.id] = u
    allTraderUsers.value = map
  } catch {
    // soft fail — селекты просто будут пустые
  }
}

function addFee() {
  editFees.value.push({ method: 'sbp', fee: '0' })
}

function addTgId() {
  const id = parseInt(newTgIdInput.value)
  if (!id || editTgIds.value.some(t => t.id === id)) {
    newTgIdInput.value = ''
    newTgLabelInput.value = ''
    return
  }
  editTgIds.value.push({ id, label: newTgLabelInput.value.trim() })
  newTgIdInput.value = ''
  newTgLabelInput.value = ''
}

function removeTgId(id: number) {
  editTgIds.value = editTgIds.value.filter(t => t.id !== id)
}

async function saveEdit() {
  if (!editing.value) return
  saving.value = true
  try {
    const fees: Record<string, number> = {}
    for (const f of editFees.value) fees[f.method] = Number(f.fee)

    const receipt_premoderation_enabled =
      editForm.premoderation_mode === 'true'
        ? true
        : editForm.premoderation_mode === 'false'
          ? false
          : null

    await merchantsService.updateAdmin(editing.value.id, {
      name: editForm.name || null,
      status: editForm.status,
      webhook_url: editForm.webhook_url || null,
      order_ttl_seconds: Number(editForm.order_ttl_seconds),
      requisite_search_timeout_ms: Number(editForm.requisite_search_timeout_ms),
      withdrawal_fee_fixed: Number(editForm.withdrawal_fee_fixed),
      fees,
      rate_config_id: editForm.rate_config_id ? Number(editForm.rate_config_id) : null,
      telegram_user_ids: editTgIds.value.map(t => ({
        id: t.id,
        label: t.label.trim() || null,
      })),
      trader_ids: editForm.trader_ids,
      group_ids: editForm.group_ids,
      receipt_premoderation_enabled,
      notify_telegram_group_id: editForm.notify_telegram_group_id
        ? Number(editForm.notify_telegram_group_id)
        : null,
      dispute_telegram_group_id: editForm.dispute_telegram_group_id
        ? Number(editForm.dispute_telegram_group_id)
        : null,
      dispute_id_mask: editForm.dispute_id_mask.trim() || null,
      cascade_mode: editForm.cascade_mode,
      cascade_group_ids: editForm.cascade_group_ids,
      unique_clients_enabled: editForm.unique_clients_enabled,
      proof_request_notify_enabled: editForm.proof_request_notify_enabled,
    })
    toast.success('Мерчант обновлён')
    await loadMerchant()  // re-fetch + reset snapshot
  } catch {
    toast.error('Ошибка')
  } finally {
    saving.value = false
  }
}

async function resetKeyForEditing() {
  if (!editing.value) return
  if (!(await confirm(`Сбросить API-ключ для мерчанта #${editing.value.id}?`))) return
  try {
    const { data } = await merchantsService.resetApiKeyAdmin(editing.value.id)
    newApiKey.value = data.api_key
    newApiSecret.value = data.api_secret
    showApiKey.value = true
  } catch {
    toast.error('Ошибка сброса ключа')
  }
}

function openEditBalance() {
  if (!editing.value) return
  balanceInitialMerchantId.value = editing.value.id
  showBalanceModal.value = true
}

function goToApiLogs() {
  if (!editing.value) return
  router.push({ path: '/admin/api-logs', query: { merchant_id: String(editing.value.id) } })
}

function goBack() {
  // Use back() when there's history (came from the list), otherwise fall
  // through to the explicit list URL (direct link / refresh).
  if (window.history.length > 1) {
    router.back()
  } else {
    router.push('/admin/merchants')
  }
}

// Re-fetch when the route id changes (e.g. clicking from one merchant
// detail to another via deep-link).
watch(routeId, (id) => {
  if (Number.isFinite(id)) loadMerchant()
})

onMounted(() => {
  loadMerchant()
  loadOptions()
})
</script>
