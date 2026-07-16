<template>
  <div>
    <PageHeader title="Реквизиты">
      <template #actions>
        <BaseButton variant="gold" @click="openCreate">+ Добавить реквизит</BaseButton>
      </template>
    </PageHeader>

    <div class="mb-4 flex flex-wrap items-center gap-2">
      <BaseInput
        v-model="filters.nickname"
        placeholder="Название / имя / реквизит"
        class="min-w-[240px] flex-1"
        @keyup.enter="page = 1; load()"
      />
      <PaymentOptionPicker
        v-model="filters.payment_option_id"
        :options="paymentOptions"
        all-label="Все банки"
        placeholder="Все банки"
        class="min-w-[200px]"
        @update:modelValue="page = 1; load()"
      />
      <BaseFilter :active-count="activeFilterCount" @apply="page = 1; load()" @reset="resetFilters">
        <BaseSelect v-model="filters.payment_method" label="Метод" :options="methodOptions" />
        <BaseSelect v-model="filters.state" label="Состояние" :options="stateOptions" />
      </BaseFilter>
    </div>

    <DataTable
      :columns="columns"
      :rows="items"
      :loading="loading"
      row-key="id"
      empty-text="У вас пока нет реквизитов"
      clickable
      @row-click="row => openDetails(row as Requisite)"
    >
      <template #cell-nickname="{ row }">
        <span class="text-text-main">{{ (row as any).nickname || '—' }}</span>
      </template>
      <template #cell-bank_name="{ row }">
        <div class="flex items-center gap-2">
          <PaymentOptionLogo
            :src="(row as Requisite).payment_option?.logo_url ?? null"
            :alt="(row as Requisite).payment_option?.name ?? (row as Requisite).bank_name"
            :size="20"
          />
          <span class="truncate text-text-main">
            {{ (row as Requisite).payment_option?.name || (row as Requisite).bank_name }}
          </span>
        </div>
      </template>
      <template #cell-account_number="{ value }">
        <button
          class="group flex items-center rounded px-1 py-0.5 transition-colors hover:bg-bg-hover active:bg-bg-card"
          title="Скопировать"
          @click.stop="copyRequisite(String(value))"
        >
          <span class="font-mono text-text-main transition-colors group-hover:text-accent">
            {{ value }}
          </span>
        </button>
      </template>
      <template #cell-payment_method="{ value }">
        <MethodBadge :method="value" />
      </template>
      <template #cell-status="{ row }">
        <div @click.stop>
          <BaseSwitch
            :model-value="(row as Requisite).status === 'enabled'"
            :loading="togglingId === (row as Requisite).id"
            :disabled="isToggleDisabled(row as Requisite)"
            :title="toggleTitle(row as Requisite)"
            size="sm"
            @change="v => toggleRequisite(row as Requisite, v)"
          />
        </div>
      </template>
      <template #cell-limits="{ value }">
        <div @click.stop>
          <RequisiteLimits :limits="value" />
        </div>
      </template>
    </DataTable>

    <!-- Details / actions modal -->
    <BaseModal v-model="showDetails" :title="detailTitle" size="md">
      <div v-if="detail" class="space-y-3 text-sm">
        <div class="grid grid-cols-2 gap-2">
          <div><span class="text-text-muted">Название:</span> <span class="text-text-main">{{ detail.nickname || '—' }}</span></div>
          <div class="flex items-center gap-2">
            <span class="text-text-muted">Банк:</span>
            <PaymentOptionLogo
              :src="detail.payment_option?.logo_url ?? null"
              :alt="detail.payment_option?.name ?? detail.bank_name"
              :size="20"
            />
            <span class="text-text-main">{{ detail.payment_option?.name || detail.bank_name }}</span>
          </div>
          <div class="col-span-2"><span class="text-text-muted">Номер счёта / карты:</span> <span class="font-mono text-text-main">{{ detail.account_number }}</span></div>
          <div class="col-span-2"><span class="text-text-muted">Имя:</span> <span class="text-text-main">{{ detail.account_holder }}</span></div>
          <div><span class="text-text-muted">Метод:</span> <MethodBadge :method="detail.payment_method" /></div>
          <div><span class="text-text-muted">Валюта:</span> <span class="text-text-main">{{ detail.currency }}</span></div>
          <div class="flex items-center gap-2">
            <span class="text-text-muted">Статус:</span>
            <BaseSwitch
              :model-value="detail.status === 'enabled'"
              :loading="togglingId === detail.id"
              :disabled="isToggleDisabled(detail)"
              :title="toggleTitle(detail)"
              size="sm"
              @change="v => toggleRequisite(detail!, v)"
            />
          </div>
        </div>

        <div v-if="detail.limits" class="rounded-lg bg-bg-card p-3 text-xs">
          <div class="mb-2 flex items-center justify-between">
            <div class="font-bold text-accent">Лимиты</div>
            <div class="text-text-muted">
              {{ detail.limits.reset_enabled ? 'Авто-сброс: вкл.' : 'Общий лимит' }}
            </div>
          </div>
          <div class="grid grid-cols-2 gap-1">
            <template v-if="detail.limits.reset_enabled">
              <div>Дневной:</div><div>{{ formatAmount(detail.limits.current_daily_turnover) }} / {{ formatAmount(detail.limits.limit_daily) }}</div>
              <div>Месячный:</div><div>{{ formatAmount(detail.limits.current_monthly_turnover) }} / {{ formatAmount(detail.limits.limit_monthly) }}</div>
            </template>
            <template v-else>
              <div>Лимит реквизита:</div><div>{{ formatAmount(detail.limits.current_daily_turnover) }} / {{ formatAmount(detail.limits.limit_daily) }}</div>
            </template>
            <div>Мин. транзакция:</div><div>{{ formatAmount(detail.limits.limit_min_transaction) }}</div>
            <div>Макс. транзакция:</div><div>{{ formatAmount(detail.limits.limit_max_transaction) }}</div>
            <div>Макс. параллельных:</div><div>{{ detail.limits.limit_max_concurrent_orders ?? '—' }}</div>
          </div>
        </div>
      </div>
      <template #footer>
        <div class="flex flex-wrap justify-end gap-2">
          <BaseButton variant="ghost" @click="showDetails = false">Закрыть</BaseButton>
          <BaseButton v-if="detail" variant="gold" @click="openDoliv(detail)">Долив</BaseButton>
          <BaseButton v-if="detail" variant="dark" @click="openEdit(detail)">Редактировать</BaseButton>
          <BaseButton v-if="detail" action="delete" variant="danger" @click="remove(detail)">Удалить</BaseButton>
        </div>
      </template>
    </BaseModal>

    <DolivCreateModal v-model="showDoliv" :requisite="dolivReq" />

    <!-- Create modal -->
    <BaseModal v-model="showCreate" title="Новый реквизит">
      <div class="space-y-4">
        <BaseInput v-model="createForm.nickname" label="Название (никнейм)" required />
        <PaymentOptionPicker
          v-model="createForm.payment_option_id"
          :options="paymentOptions"
          label="Банк"
          :disabled="loadingPaymentOptions"
        />
        <BaseInput v-model="createForm.account_number" label="Номер счёта / карты" required />
        <BaseInput v-model="createForm.account_holder" label="Имя владельца" required />
        <BaseSelect
          v-model="createForm.payment_method"
          label="Метод оплаты"
          :options="availableMethodsForCreate"
          :disabled="!createForm.payment_option_id"
        />
        <BaseSelect
          :model-value="createForm.trader_priority"
          label="Приоритет (1–3)"
          :options="requisitePriorityOptions"
          @update:model-value="v => (createForm.trader_priority = Number(v))"
        />
        <BaseSelect v-model="createForm.currency" label="Валюта" :options="currencyOptions" disabled />
        <p v-if="!createForm.payment_option_id" class="text-xs text-text-muted">
          Сначала выберите банк, чтобы увидеть доступные методы оплаты и валюту.
        </p>

        <div class="rounded-lg border border-border bg-bg-card p-3">
          <div class="mb-3 flex items-center justify-between gap-3">
            <div class="text-sm font-semibold text-accent">Лимиты</div>
            <label class="flex items-center gap-2">
              <span class="text-xs text-text-muted">Авто-сброс</span>
              <BaseSwitch v-model="createForm.reset_enabled" />
            </label>
          </div>
          <p
            v-if="createForm.reset_enabled"
            class="mb-3 text-xs text-text-muted"
          >
            Дневной счётчик обнуляется каждую полночь UTC, месячный — 1 числа.
          </p>
          <div class="grid grid-cols-2 gap-3">
            <BaseInput
              v-if="createForm.reset_enabled"
              v-model="createForm.limit_daily"
              label="Дневной"
              type="number"
              placeholder="100000"
            />
            <BaseInput
              v-if="createForm.reset_enabled"
              v-model="createForm.limit_monthly"
              label="Месячный"
              type="number"
              placeholder="1000000"
            />
            <BaseInput
              v-else
              v-model="createForm.limit_total"
              label="Лимит реквизита"
              type="number"
              placeholder="1000000"
              class="col-span-2"
            />
            <BaseInput v-model="createForm.limit_min_transaction" label="Мин. транзакция" type="number" placeholder="100" />
            <BaseInput v-model="createForm.limit_max_transaction" label="Макс. транзакция" type="number" placeholder="50000" />
            <BaseInput v-model="createForm.limit_max_concurrent_orders" label="Макс. параллельных ордеров" type="number" placeholder="—" />
          </div>
        </div>
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showCreate = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="saving" @click="createRequisite">Создать</BaseButton>
      </template>
    </BaseModal>

    <!-- Edit modal -->
    <BaseModal v-model="showEdit" title="Редактирование реквизита">
      <div v-if="editing" class="space-y-4">
        <BaseInput v-model="editForm.nickname" label="Название (никнейм)" />
        <PaymentOptionPicker
          v-model="editForm.payment_option_id"
          :options="paymentOptions"
          label="Банк"
          :disabled="loadingPaymentOptions"
        />
        <BaseInput v-model="editForm.account_number" label="Номер счёта" />
        <BaseInput v-model="editForm.account_holder" label="Имя" />
        <BaseSelect
          v-model="editForm.payment_method"
          label="Метод оплаты"
          :options="availableMethodsForEdit"
          :disabled="!editForm.payment_option_id"
        />
        <BaseSelect
          :model-value="editForm.trader_priority"
          label="Приоритет (1–3)"
          :options="requisitePriorityOptions"
          @update:model-value="v => (editForm.trader_priority = Number(v))"
        />

        <div class="rounded-lg border border-border bg-bg-card p-3">
          <div class="mb-3 flex items-center justify-between gap-3">
            <div class="text-sm font-semibold text-accent">Лимиты</div>
            <label class="flex items-center gap-2">
              <span class="text-xs text-text-muted">Авто-сброс</span>
              <BaseSwitch v-model="editForm.reset_enabled" />
            </label>
          </div>
          <p
            v-if="editForm.reset_enabled"
            class="mb-3 text-xs text-text-muted"
          >
            Дневной счётчик обнуляется каждую полночь UTC, месячный — 1 числа.
          </p>
          <div class="grid grid-cols-2 gap-3">
            <BaseInput
              v-if="editForm.reset_enabled"
              v-model="editForm.limit_daily"
              label="Дневной"
              type="number"
            />
            <BaseInput
              v-if="editForm.reset_enabled"
              v-model="editForm.limit_monthly"
              label="Месячный"
              type="number"
            />
            <BaseInput
              v-else
              v-model="editForm.limit_total"
              label="Лимит реквизита"
              type="number"
              class="col-span-2"
            />
            <BaseInput v-model="editForm.limit_min_transaction" label="Мин. транзакция" type="number" />
            <BaseInput v-model="editForm.limit_max_transaction" label="Макс. транзакция" type="number" />
            <BaseInput v-model="editForm.limit_max_concurrent_orders" label="Макс. параллельных ордеров" type="number" />
          </div>
        </div>
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showEdit = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="saving" @click="saveEdit">Сохранить</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, onMounted, watch } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseFilter from '@/components/ui/BaseFilter.vue'
import BaseSwitch from '@/components/ui/BaseSwitch.vue'
import DolivCreateModal from '@/components/doliv/DolivCreateModal.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import RequisiteLimits from '@/components/ui/RequisiteLimits.vue'
import PaymentOptionPicker from '@/components/ui/PaymentOptionPicker.vue'
import PaymentOptionLogo from '@/components/ui/PaymentOptionLogo.vue'
import { requisitesService } from '@/api/services/requisites.service'
import { paymentsService } from '@/api/services/payments.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { useActiveStats } from '@/composables/useActiveStats'
import { formatAmount, copyToClipboard } from '@/utils/format'
import { normalizePhone, isPhoneMethod } from '@/utils/phone'
import {
  paymentMethodOptions,
  paymentMethodOptionsWithAll,
  currencyOptions,
  requisitePriorityOptions,
} from '@/constants'
import type { Requisite, RequisiteCreate, PaymentMethod, Currency, PaymentOption } from '@/types'

const toast = useToast()
const { confirm } = useConfirm()
const { refresh: refreshActiveStats } = useActiveStats()

const loading = ref(true)
const saving = ref(false)
const togglingId = ref<number | null>(null)
const items = ref<Requisite[]>([])
const page = ref(1)

const filters = reactive({
  nickname: '',
  // Bank filter is driven by the same catalog picker as the create form; the
  // selected PaymentOption maps to its (denormalised) bank_name server-side.
  payment_option_id: null as number | null,
  payment_method: '' as PaymentMethod | '',
  state: '' as '' | 'active' | 'disabled' | 'blocked' | 'archived',
})

const methodOptions = paymentMethodOptionsWithAll
// «Состояние» — понятные состояния вместо сырого status/is_active.
const stateOptions = [
  { value: '', label: 'Все' },
  { value: 'active', label: 'Включён' },
  { value: 'disabled', label: 'Выключен' },
  { value: 'blocked', label: 'Заблокирован' },
  { value: 'archived', label: 'В архиве' },
]

const paymentOptions = ref<PaymentOption[]>([])
const loadingPaymentOptions = ref(false)

const showCreate = ref(false)
const createForm = reactive({
  nickname: '',
  payment_option_id: null as number | null,
  account_number: '',
  account_holder: '',
  payment_method: '' as PaymentMethod | '',
  currency: 'RUB' as Currency,
  // When reset_enabled is true, daily/monthly are used as separate caps.
  // When false, `limit_total` is the only field shown; we mirror it into
  // both daily and monthly server-side fields so pooling checks pass.
  limit_total: '',
  limit_daily: '',
  limit_monthly: '',
  limit_min_transaction: '',
  limit_max_transaction: '',
  limit_max_concurrent_orders: '',
  reset_enabled: false,
  trader_priority: 1 as number,
})

const showEdit = ref(false)
const editing = ref<Requisite | null>(null)
const editForm = reactive({
  nickname: '',
  payment_option_id: null as number | null,
  account_number: '',
  account_holder: '',
  payment_method: '' as PaymentMethod | '',
  limit_total: '',
  limit_daily: '',
  limit_monthly: '',
  limit_min_transaction: '',
  limit_max_transaction: '',
  limit_max_concurrent_orders: '',
  reset_enabled: false,
  trader_priority: 1 as number,
})

function methodsForOption(optionId: number | null) {
  if (!optionId) return []
  const found = paymentOptions.value.find(o => o.id === optionId)
  if (!found) return []
  const supported = new Set(found.supported_methods)
  return paymentMethodOptions.filter(m => m.value && supported.has(m.value as PaymentMethod))
}

const availableMethodsForCreate = computed(() => methodsForOption(createForm.payment_option_id))
const availableMethodsForEdit = computed(() => methodsForOption(editForm.payment_option_id))

const showDetails = ref(false)
const detail = ref<Requisite | null>(null)
const detailTitle = computed(() => detail.value ? (detail.value.nickname || detail.value.bank_name) : 'Реквизит')

// Долив — request a refill against this requisite.
const showDoliv = ref(false)
const dolivReq = ref<{
  id: number
  currency: string
  account_number?: string
  remaining?: number | null
  bank_name?: string | null
  payment_option_name?: string | null
  logo_url?: string | null
  payment_method?: string
} | null>(null)
function openDoliv(req: Requisite) {
  // Remaining-to-daily-limit drives the modal's range hint.
  const lim = req.limits
  const remaining = lim ? Number(lim.limit_daily) - Number(lim.current_daily_turnover) : null
  dolivReq.value = {
    id: req.id,
    currency: req.currency,
    account_number: req.account_number,
    remaining: remaining != null && remaining > 0 ? remaining : null,
    bank_name: req.bank_name,
    payment_option_name: req.payment_option?.name ?? null,
    logo_url: req.payment_option?.logo_url ?? null,
    payment_method: req.payment_method,
  }
  showDetails.value = false
  showDoliv.value = true
}

const columns: Column[] = [
  { key: 'nickname', label: 'Название' },
  { key: 'bank_name', label: 'Банк' },
  { key: 'account_number', label: 'Реквизит' },
  { key: 'account_holder', label: 'Имя' },
  { key: 'payment_method', label: 'Метод' },
  { key: 'limits', label: 'Лимит (дн.)', cellClass: 'py-1' },
  { key: 'status', label: 'Статус' },
]

const activeFilterCount = computed(() => {
  let n = 0
  if (filters.payment_method) n++
  if (filters.state) n++
  return n
})

function resetFilters() {
  filters.nickname = ''
  filters.payment_option_id = null
  filters.payment_method = ''
  filters.state = ''
  page.value = 1
  load()
}

async function load() {
  loading.value = true
  try {
    const params: Record<string, any> = {}
    if (filters.nickname.trim()) params.nickname = filters.nickname.trim()
    // The picker yields a payment_option_id; the list endpoint filters by bank
    // name (denormalised to PaymentOption.name), so map the selection to it.
    if (filters.payment_option_id) {
      const opt = paymentOptions.value.find(o => o.id === filters.payment_option_id)
      if (opt) params.bank = opt.name
    }
    if (filters.payment_method) params.payment_method = filters.payment_method
    if (filters.state) params.state = filters.state
    const { data } = await requisitesService.listMy(params)
    items.value = data
  } catch { toast.error('Ошибка загрузки') }
  finally { loading.value = false }
}

function openDetails(row: Requisite) {
  detail.value = row
  showDetails.value = true
}

function openCreate() {
  Object.assign(createForm, {
    nickname: '',
    payment_option_id: null as number | null,
    account_number: '',
    account_holder: '',
    payment_method: '' as PaymentMethod | '',
    currency: 'RUB' as Currency,
    limit_total: '',
    limit_daily: '',
    limit_monthly: '',
    limit_min_transaction: '',
    limit_max_transaction: '',
    limit_max_concurrent_orders: '',
    reset_enabled: false,
    trader_priority: 1 as number,
  })
  showCreate.value = true
}

async function loadPaymentOptions() {
  if (paymentOptions.value.length || loadingPaymentOptions.value) return
  loadingPaymentOptions.value = true
  try {
    const { data } = await paymentsService.getOptions()
    paymentOptions.value = data
  } catch {
    toast.error('Не удалось загрузить список банков')
  } finally {
    loadingPaymentOptions.value = false
  }
}

watch(
  () => createForm.payment_option_id,
  (newId) => {
    const opt = paymentOptions.value.find(o => o.id === newId)
    if (!opt) return
    createForm.currency = opt.currency
    if (
      !createForm.payment_method ||
      !opt.supported_methods.includes(createForm.payment_method)
    ) {
      createForm.payment_method = (opt.supported_methods[0] as PaymentMethod) || ''
    }
  },
)

watch(
  () => editForm.payment_option_id,
  (newId, oldId) => {
    if (newId === oldId) return
    const opt = paymentOptions.value.find(o => o.id === newId)
    if (!opt) return
    if (
      !editForm.payment_method ||
      !opt.supported_methods.includes(editForm.payment_method)
    ) {
      editForm.payment_method = (opt.supported_methods[0] as PaymentMethod) || ''
    }
  },
)

function toOptionalNumber(v: string): number | undefined {
  if (v === '' || v === null || v === undefined) return undefined
  const n = Number(v)
  return Number.isFinite(n) ? n : undefined
}

function buildLimitsPayload(src: {
  limit_total: string
  limit_daily: string
  limit_monthly: string
  limit_min_transaction: string
  limit_max_transaction: string
  limit_max_concurrent_orders: string
  reset_enabled: boolean
}): RequisiteCreate['limits'] | undefined {
  const out: Record<string, number | boolean> = {}
  if (src.reset_enabled) {
    const d = toOptionalNumber(src.limit_daily); if (d !== undefined) out.limit_daily = d
    const m = toOptionalNumber(src.limit_monthly); if (m !== undefined) out.limit_monthly = m
  } else {
    // Reset off → single общий лимит. Mirror it into both server-side fields
    // so pooling checks remain consistent (current_daily and current_monthly
    // both increment with each successful order).
    const t = toOptionalNumber(src.limit_total)
    if (t !== undefined) {
      out.limit_daily = t
      out.limit_monthly = t
    }
  }
  const min = toOptionalNumber(src.limit_min_transaction); if (min !== undefined) out.limit_min_transaction = min
  const max = toOptionalNumber(src.limit_max_transaction); if (max !== undefined) out.limit_max_transaction = max
  const conc = toOptionalNumber(src.limit_max_concurrent_orders); if (conc !== undefined) out.limit_max_concurrent_orders = conc
  // Always send the flag so traders can toggle it off too.
  out.reset_enabled = src.reset_enabled
  return out as RequisiteCreate['limits']
}

function validateLimitsForm(src: {
  limit_total: string
  limit_daily: string
  limit_monthly: string
  reset_enabled: boolean
}): string | null {
  // When reset is off the trader fills a single «Лимит реквизита». Without
  // explicit validation the empty field would silently make the request omit
  // both ``limit_daily`` and ``limit_monthly`` — and Pydantic on the server
  // would then default them to 100k / 1M, leaving the trader with an
  // effective lifetime cap of 100k regardless of what the placeholder hinted.
  if (!src.reset_enabled) {
    const t = toOptionalNumber(src.limit_total)
    if (t === undefined || t <= 0) return 'Укажите лимит реквизита'
    return null
  }
  // Reset on — both daily and monthly required.
  const d = toOptionalNumber(src.limit_daily)
  if (d === undefined || d <= 0) return 'Укажите дневной лимит'
  const m = toOptionalNumber(src.limit_monthly)
  if (m === undefined || m <= 0) return 'Укажите месячный лимит'
  return null
}

async function createRequisite() {
  if (!createForm.payment_option_id) {
    toast.error('Выберите банк')
    return
  }
  if (!createForm.payment_method) {
    toast.error('Выберите метод оплаты')
    return
  }
  const limitsErr = validateLimitsForm(createForm)
  if (limitsErr) {
    toast.error(limitsErr)
    return
  }
  if (isPhoneMethod(createForm.payment_method)) {
    createForm.account_number = normalizePhone(createForm.account_number)
  }
  saving.value = true
  try {
    await requisitesService.create({
      nickname: createForm.nickname.trim() || null,
      payment_option_id: createForm.payment_option_id,
      account_number: createForm.account_number,
      account_holder: createForm.account_holder,
      payment_method: createForm.payment_method as PaymentMethod,
      currency: createForm.currency,
      limits: buildLimitsPayload(createForm),
      trader_priority: createForm.trader_priority,
    })
    toast.success('Реквизит создан')
    showCreate.value = false
    load()
    refreshActiveStats()
  } catch (e: any) {
    toast.error(e.response?.data?.detail || 'Ошибка создания')
  } finally { saving.value = false }
}

function openEdit(row: Requisite) {
  editing.value = row
  const reset = row.limits?.reset_enabled ?? false
  // For reset-off requisites the daily and monthly limits are kept in sync,
  // so picking either gives the lifetime cap.
  const total = row.limits?.limit_daily ?? row.limits?.limit_monthly ?? null
  Object.assign(editForm, {
    nickname: row.nickname ?? '',
    payment_option_id: row.payment_option_id ?? null,
    account_number: row.account_number,
    account_holder: row.account_holder,
    payment_method: row.payment_method,
    limit_total: !reset && total != null ? String(total) : '',
    limit_daily: row.limits?.limit_daily != null ? String(row.limits.limit_daily) : '',
    limit_monthly: row.limits?.limit_monthly != null ? String(row.limits.limit_monthly) : '',
    limit_min_transaction: row.limits?.limit_min_transaction != null ? String(row.limits.limit_min_transaction) : '',
    limit_max_transaction: row.limits?.limit_max_transaction != null ? String(row.limits.limit_max_transaction) : '',
    limit_max_concurrent_orders: row.limits?.limit_max_concurrent_orders != null ? String(row.limits.limit_max_concurrent_orders) : '',
    reset_enabled: reset,
    trader_priority: row.trader_priority ?? 1,
  })
  showDetails.value = false
  showEdit.value = true
}

async function saveEdit() {
  if (!editing.value) return
  const limitsErr = validateLimitsForm(editForm)
  if (limitsErr) {
    toast.error(limitsErr)
    return
  }
  if (isPhoneMethod(editForm.payment_method) && editForm.account_number) {
    editForm.account_number = normalizePhone(editForm.account_number)
  }
  saving.value = true
  try {
    await requisitesService.updateMy(editing.value.id, {
      nickname: editForm.nickname.trim() || null,
      payment_option_id: editForm.payment_option_id ?? undefined,
      account_number: editForm.account_number || undefined,
      account_holder: editForm.account_holder || undefined,
      payment_method: (editForm.payment_method || undefined) as PaymentMethod | undefined,
      limits: buildLimitsPayload(editForm),
      trader_priority: editForm.trader_priority,
    })
    toast.success('Реквизит обновлён')
    showEdit.value = false
    load()
    refreshActiveStats()
  } catch (e: any) {
    toast.error(e.response?.data?.detail || 'Ошибка сохранения')
  } finally { saving.value = false }
}

async function remove(row: Requisite) {
  if (!(await confirm(`Удалить реквизит ${row.nickname || row.bank_name} (${row.account_number})?`))) return
  try {
    await requisitesService.deleteMy(row.id)
    showDetails.value = false
    toast.success('Реквизит удалён')
    load()
    refreshActiveStats()
  } catch { toast.error('Ошибка удаления') }
}

function isToggleDisabled(row: Requisite): boolean {
  // Свич недоступен если реквизит заблокирован админом, заархивирован,
  // а также если админ не активировал реквизит.
  return row.status === 'blocked' || row.status === 'archived' || row.is_archived || !row.is_active
}

function toggleTitle(row: Requisite): string {
  if (row.is_archived || row.status === 'archived') return 'Реквизит в архиве'
  if (row.status === 'blocked') return 'Реквизит заблокирован администрацией'
  if (!row.is_active) return 'Реквизит не активирован администрацией'
  return row.status === 'enabled' ? 'Выключить приём заявок' : 'Включить приём заявок'
}

async function toggleRequisite(row: Requisite, enabled: boolean) {
  if (togglingId.value === row.id) return
  togglingId.value = row.id
  try {
    const { data } = enabled
      ? await requisitesService.enableMy(row.id)
      : await requisitesService.disableMy(row.id)

    const idx = items.value.findIndex(r => r.id === row.id)
    if (idx !== -1) items.value[idx] = data
    if (detail.value && detail.value.id === row.id) detail.value = data

    toast.success(enabled ? 'Реквизит включён' : 'Реквизит выключен')
    // Sidebar "active requisites" badge depends on this status — refresh
    // so the operator doesn't have to wait for the next 30s poll tick.
    refreshActiveStats()
  } catch (e: any) {
    toast.error(e.response?.data?.detail || 'Не удалось изменить статус')
  } finally {
    togglingId.value = null
  }
}

async function copyRequisite(text: string) {
  try {
    await copyToClipboard(text)
    toast.success('Скопировано')
  } catch {
    toast.error('Не удалось скопировать')
  }
}

onMounted(() => {
  load()
  loadPaymentOptions()
})
</script>
