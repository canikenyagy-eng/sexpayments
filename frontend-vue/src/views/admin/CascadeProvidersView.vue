<template>
  <div>
    <PageHeader title="Провайдеры каскада">
      <template #actions>
        <BaseButton variant="gold" @click="openCreate">+ Добавить провайдера</BaseButton>
      </template>
    </PageHeader>

    <div class="mb-4 flex gap-3">
      <BaseInput v-model="search" placeholder="Поиск по коду или названию" class="max-w-md flex-1" />
    </div>

    <div v-if="loading" class="py-12 text-center text-text-muted">Загрузка…</div>

    <div v-else-if="filtered.length" class="space-y-3">
      <div
        v-for="p in filtered"
        :key="p.id"
        class="cursor-pointer rounded-2xl border border-border bg-bg-surface p-4 transition hover:border-accent"
        @click="openEdit(p)"
      >
        <div class="flex flex-wrap items-center gap-3">
          <div class="min-w-0 flex-1">
            <div class="flex items-center gap-2">
              <h3 class="truncate font-bold text-text-main">{{ p.name }}</h3>
              <span class="rounded-full bg-bg-default px-2 py-0.5 text-xs text-text-muted">
                {{ p.code }}
              </span>
              <CascadeAdapterBadge
                :adapter="adapterByCode(p.adapter_type)"
                :code="p.adapter_type"
              />
              <span
                class="rounded-full bg-bg-default px-2 py-0.5 text-xs text-text-muted"
                :class="{ '!bg-accent/15 !text-accent': p.rate_source === 'platform' }"
              >
                {{ p.rate_source === 'platform' ? 'наш курс' : 'курс провайдера' }}
              </span>
              <span
                v-if="!p.is_active"
                class="rounded-full bg-status-warning/10 px-2 py-0.5 text-xs text-status-warning"
              >
                disabled
              </span>
              <span
                v-if="p.disabled_until"
                class="rounded-full bg-status-danger/10 px-2 py-0.5 text-xs text-status-danger"
              >
                circuit-open
              </span>
            </div>
            <p class="text-xs text-text-muted">{{ p.base_url }}</p>
          </div>
          <div class="flex items-center gap-4 text-sm text-text-secondary">
            <span>weight {{ p.priority_weight }}</span>
            <span>tmo {{ p.request_timeout_ms }} ms</span>
            <BaseButton
              variant="dark"
              size="sm"
              title="Проверить провайдера живым запросом"
              @click.stop="openTestIssue(p)"
            >
              Тест
            </BaseButton>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="py-12 text-center text-text-muted">
      {{ search ? 'Ничего не найдено' : 'Провайдеров пока нет — добавьте первого.' }}
    </div>

    <!--
      "Проба" — full e2e against the provider's real API: synthesise an
      order, dispatch to adapter.issue_requisite, then auto-cancel.
      Lives OUTSIDE the v-if/v-else-if/v-else chain above so it doesn't
      break Vue's adjacency rule for those siblings; placement here also
      keeps it grouped with the other modals (edit dialog below).
    -->
    <BaseModal
      v-model="showTestIssue"
      :title="testIssueProvider ? `Тестовая выдача: ${testIssueProvider.name}` : 'Тестовая выдача'"
      size="md"
    >
      <template #default>
        <div class="space-y-4">
          <p class="text-xs text-text-muted">
            Реальный запрос к провайдеру — резервирует трейдера на их
            стороне, потом автоматически отменяет (если включено).
            Никаких записей в нашу БД не делает.
          </p>
          <div class="grid grid-cols-2 gap-3">
            <BaseInput
              v-model.number="testIssueForm.amount"
              type="number"
              step="0.01"
              label="Сумма (фиат)"
            />
            <BaseSelect
              v-model="testIssueForm.payment_method"
              label="Метод"
              :options="testIssueMethodOptions"
            />
          </div>
          <BaseInput
            v-model="testIssueForm.payment_option_code"
            label="PaymentOption.code (опционально)"
            placeholder="sber, tbank, alfa…"
          />
          <label class="flex items-center gap-2 text-sm text-text-secondary">
            <input
              v-model="testIssueForm.auto_cancel"
              type="checkbox"
              class="h-4 w-4 accent-accent"
            />
            Автоматически отменить после успешной выдачи
          </label>

          <div v-if="testIssueResult" class="space-y-2 rounded-xl border border-border bg-bg-default p-3">
            <div class="flex items-center gap-2 text-sm">
              <span
                :class="testIssueResult.ok ? 'text-status-success' : 'text-status-danger'"
                class="font-bold"
              >
                {{ testIssueResult.ok ? '✓' : '✗' }}
                {{ testIssueResult.outcome }}
              </span>
              <span class="text-text-muted">{{ testIssueResult.summary }}</span>
              <span v-if="testIssueResult.latency_ms" class="ml-auto text-xs text-text-muted">
                {{ testIssueResult.latency_ms }} ms
              </span>
            </div>

            <div v-if="testIssueResult.requisite" class="grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
              <div><b>id:</b> {{ testIssueResult.requisite.external_order_id }}</div>
              <div><b>amount:</b> {{ testIssueResult.requisite.amount_fiat }}</div>
              <div><b>bank:</b> {{ testIssueResult.requisite.bank_name }}</div>
              <div><b>method:</b> {{ testIssueResult.requisite.payment_method }}</div>
              <div><b>holder:</b> {{ testIssueResult.requisite.account_holder }}</div>
              <div><b>account:</b> {{ testIssueResult.requisite.account_number }}</div>
              <div v-if="testIssueResult.requisite.provider_rate">
                <b>rate:</b> {{ testIssueResult.requisite.provider_rate }}
              </div>
              <div><b>expires:</b> {{ testIssueResult.requisite.expires_at }}</div>
            </div>

            <div
              v-if="testIssueResult.outcome === 'refusal'"
              class="text-xs text-status-warning"
            >
              <b>{{ testIssueResult.refusal_code }}:</b>
              {{ testIssueResult.refusal_message }}
            </div>

            <div
              v-if="testIssueResult.auto_cancel"
              class="text-xs text-text-muted"
            >
              auto-cancel:
              {{ testIssueResult.auto_cancel.ok ? 'ok' : 'failed' }}
              <span v-if="testIssueResult.auto_cancel.error">
                — {{ testIssueResult.auto_cancel.error }}
              </span>
            </div>

            <details
              v-if="testIssueResult.request_preview"
              class="text-xs"
              open
            >
              <summary class="cursor-pointer text-text-muted">
                request sent to provider
              </summary>
              <div v-if="testIssueResult.request_preview.error" class="text-status-warning">
                preview error: {{ testIssueResult.request_preview.error }}
              </div>
              <div v-else class="space-y-2">
                <div>
                  <b>{{ testIssueResult.request_preview.method }}</b>
                  <span class="ml-1 break-all">{{ testIssueResult.request_preview.url }}</span>
                </div>
                <div
                  v-if="testIssueResult.request_preview.params
                    && Object.keys(testIssueResult.request_preview.params).length"
                >
                  <div class="font-bold text-text-muted">query params</div>
                  <pre class="overflow-x-auto whitespace-pre-wrap break-all text-[10px]">{{ JSON.stringify(testIssueResult.request_preview.params, null, 2) }}</pre>
                </div>
                <div
                  v-if="testIssueResult.request_preview.headers
                    && Object.keys(testIssueResult.request_preview.headers).length"
                >
                  <div class="font-bold text-text-muted">headers (auth/signature замаскированы)</div>
                  <pre class="overflow-x-auto whitespace-pre-wrap break-all text-[10px]">{{ JSON.stringify(testIssueResult.request_preview.headers, null, 2) }}</pre>
                </div>
                <div v-if="testIssueResult.request_preview.body">
                  <div class="font-bold text-text-muted">body</div>
                  <pre class="overflow-x-auto whitespace-pre-wrap break-all text-[10px]">{{ JSON.stringify(testIssueResult.request_preview.body, null, 2) }}</pre>
                </div>
              </div>
            </details>

            <details v-if="testIssueResult.raw" class="text-xs">
              <summary class="cursor-pointer text-text-muted">raw response</summary>
              <pre class="overflow-x-auto whitespace-pre-wrap break-all text-[10px]">{{ JSON.stringify(testIssueResult.raw, null, 2) }}</pre>
            </details>
          </div>
        </div>
      </template>
      <template #footer>
        <BaseButton variant="dark" @click="showTestIssue = false">Закрыть</BaseButton>
        <BaseButton
          variant="gold"
          :loading="testIssueLoading"
          @click="runTestIssue"
        >
          Запустить
        </BaseButton>
      </template>
    </BaseModal>

    <BaseModal
      v-model="showModal"
      :title="editing ? `Провайдер: ${editing.name}` : 'Новый провайдер'"
      size="lg"
    >
      <BaseTabs v-model="activeTab" :tabs="tabs">
        <template #general>
          <div class="space-y-3 pt-2">
            <BaseInput
              v-model="form.code"
              :disabled="!!editing"
              label="Код (уникальный)"
              placeholder="legacy_crypto_main"
            />
            <BaseInput v-model="form.name" label="Название" placeholder="LegacyCrypto · prod" />
            <BaseSelect
              v-model="form.adapter_type"
              label="Тип интеграции"
              :options="adapterOptions"
              :disabled="!!editing"
            />
            <p v-if="selectedAdapter?.description" class="text-xs text-text-muted">
              {{ selectedAdapter.description }}
            </p>
            <BaseInput v-model="form.base_url" label="Base URL" placeholder="https://api.provider.com" />
            <label class="flex items-center gap-2 text-sm text-text-secondary">
              <input v-model="form.is_active" type="checkbox" class="h-4 w-4 accent-accent" />
              Активен
            </label>
            <div class="grid grid-cols-2 gap-3">
              <BaseInput v-model.number="form.request_timeout_ms" type="number" label="request_timeout_ms" />
              <BaseInput v-model.number="form.cancel_timeout_ms" type="number" label="cancel_timeout_ms" />
            </div>
            <BaseInput v-model.number="form.priority_weight" type="number" label="Priority weight" />
          </div>
        </template>

        <template #credentials>
          <div class="space-y-4 pt-2">
            <p class="text-xs text-text-muted">
              Все секреты шифруются на бэке. Оставьте поле пустым, чтобы не менять.
            </p>
            <div v-if="!selectedAdapter" class="text-sm italic text-text-muted">
              Выберите тип интеграции на вкладке «Основные», чтобы увидеть нужные ключи.
            </div>
            <AdapterSettingsForm
              v-else
              v-model="credentialsModel"
              :schema="selectedAdapter.credentials_schema"
            />
            <div v-if="!editing">
              <BaseInput
                v-model.number="form.initial_balance_usdt"
                type="number"
                step="0.01"
                label="Начальный USDT баланс"
                placeholder="0"
              />
            </div>
          </div>
        </template>

        <template #fees>
          <div class="space-y-3 pt-2">
            <p class="text-xs text-text-muted">
              Комиссия провайдера за каждый метод (в %). Используется при расчёте нашей прибыли:
              <code>our_profit = merchant_fee − provider_fee</code>.
            </p>
            <div class="grid grid-cols-1 gap-3 md:grid-cols-3">
              <div v-for="method in paymentMethods" :key="method.value">
                <label class="mb-1 block text-sm font-bold text-text-secondary">
                  {{ method.label }}
                </label>
                <div class="flex items-center gap-2">
                  <BaseInput
                    :model-value="form.fees[method.value] ?? 0"
                    type="number"
                    step="0.01"
                    min="0"
                    max="100"
                    class="flex-1"
                    @update:model-value="(v) => updateFee(method.value, v)"
                  />
                  <span class="text-text-muted">%</span>
                </div>
              </div>
            </div>
          </div>
        </template>

        <template #rates>
          <div class="space-y-4 pt-2">
            <div class="grid grid-cols-1 gap-2">
              <label
                class="flex cursor-pointer items-start gap-3 rounded-xl border border-border bg-bg-default p-3 transition hover:border-accent"
                :class="{ '!border-accent': form.rate_source === 'provider' }"
              >
                <input
                  type="radio"
                  value="provider"
                  v-model="form.rate_source"
                  class="mt-0.5 accent-accent"
                  :disabled="selectedAdapter && !selectedAdapter.supports_provider_rate"
                />
                <div>
                  <div class="font-bold text-text-main">Курс провайдера</div>
                  <p class="text-xs text-text-muted">
                    Берём курс из их ответа (например, LegacyCrypto.rate_with_commission или rub_amount / usdt_amount).
                  </p>
                  <p
                    v-if="selectedAdapter && !selectedAdapter.supports_provider_rate"
                    class="mt-1 text-xs text-status-warning"
                  >
                    Этот адаптер не квотирует свой курс — используйте «Наш курс».
                  </p>
                </div>
              </label>
              <label
                class="flex cursor-pointer items-start gap-3 rounded-xl border border-border bg-bg-default p-3 transition hover:border-accent"
                :class="{ '!border-accent': form.rate_source === 'platform' }"
              >
                <input
                  type="radio"
                  value="platform"
                  v-model="form.rate_source"
                  class="mt-0.5 accent-accent"
                />
                <div class="flex-1">
                  <div class="font-bold text-text-main">Наш курс</div>
                  <p class="text-xs text-text-muted">
                    Используем выбранный RateConfig (тот же справочник, что у мерчанта).
                  </p>
                </div>
              </label>
            </div>

            <div v-if="form.rate_source === 'platform'">
              <BaseSelect
                v-model.number="form.rate_config_id"
                label="Источник курса (RateConfig)"
                :options="rateConfigOptions"
              />
              <p
                v-if="!form.rate_config_id"
                class="mt-1 text-xs text-status-warning"
              >
                Выберите конфиг курса — без него каскадные ордера не будут материализоваться.
              </p>
            </div>
          </div>
        </template>

        <template #limits>
          <div class="space-y-3 pt-2">
            <div class="grid grid-cols-2 gap-3">
              <BaseInput v-model.number="form.min_amount_fiat" type="number" label="Мин. сумма (фиат)" />
              <BaseInput v-model.number="form.max_amount_fiat" type="number" label="Макс. сумма (фиат)" />
            </div>
          </div>
        </template>

        <template #breaker>
          <div class="space-y-3 pt-2">
            <p class="text-xs text-text-muted">
              Циркут-брейкер автоматически отключает провайдера, если в окне {{ form.cb_window_seconds }} с
              получено ≥ {{ form.cb_threshold_failures }} ошибок и доля ошибок ≥ {{ form.cb_threshold_rate }}.
              Кулдаун {{ form.cb_cooldown_seconds }} с.
            </p>
            <div class="grid grid-cols-2 gap-3">
              <BaseInput v-model.number="form.cb_window_seconds" type="number" label="Окно (сек)" />
              <BaseInput v-model.number="form.cb_threshold_failures" type="number" label="Минимум ошибок" />
              <BaseInput v-model.number="form.cb_threshold_rate" type="number" step="0.05" label="Доля ошибок (0–1)" />
              <BaseInput v-model.number="form.cb_cooldown_seconds" type="number" label="Кулдаун (сек)" />
            </div>
          </div>
        </template>

        <template #settings>
          <div class="pt-2">
            <AdapterSettingsForm
              v-if="selectedAdapter"
              :schema="selectedAdapter.settings_schema"
              v-model="form.settings"
            />
            <div v-else class="text-sm text-text-muted">
              Выберите тип интеграции, чтобы увидеть настройки.
            </div>
          </div>
        </template>

        <template v-if="editing" #balance>
          <div class="space-y-4 pt-2">
            <div
              class="rounded-2xl border border-border bg-bg-default p-4"
            >
              <div class="flex items-center justify-between">
                <div>
                  <div class="text-xs uppercase tracking-wide text-text-muted">
                    Баланс у провайдера (live)
                  </div>
                  <div class="mt-1 font-bold text-text-main">
                    <span v-if="upstreamLoading">Загрузка…</span>
                    <span v-else-if="upstreamSupported === false">
                      Не поддерживается этим адаптером
                    </span>
                    <span v-else-if="upstreamBalance !== null">
                      {{ upstreamBalance }} USDT
                    </span>
                    <span v-else class="text-text-muted">—</span>
                  </div>
                </div>
                <BaseButton variant="dark" :loading="upstreamLoading" @click="refreshUpstream">
                  Обновить
                </BaseButton>
              </div>
            </div>

            <div>
              <h4 class="mb-2 text-sm font-bold text-text-main">Корректировка USDT</h4>
              <p class="mb-3 text-xs text-text-muted">
                Положительная дельта — пополнение виртуального трейдера, отрицательная — списание.
              </p>
              <div class="grid grid-cols-3 gap-3">
                <BaseInput v-model.number="balanceForm.delta" type="number" step="0.01" label="Дельта USDT" />
                <BaseInput v-model="balanceForm.reason" label="Причина" />
                <BaseButton variant="dark" :loading="adjusting" @click="adjustBalance">Применить</BaseButton>
              </div>
            </div>
          </div>
        </template>
      </BaseTabs>

      <template #footer>
        <button
          v-if="editing"
          type="button"
          class="mr-auto inline-flex items-center justify-center gap-2 rounded-xl font-bold transition-all duration-200 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed px-4 py-2.5 text-sm bg-status-danger/15 text-status-danger border border-status-danger/30 hover:bg-status-danger/25 hover:-translate-y-px"
          @click="askDelete(editing)"
        >
          Удалить
        </button>
        <BaseButton variant="dark" @click="showModal = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="saving" @click="save">Сохранить</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseTabs from '@/components/ui/BaseTabs.vue'
import AdapterSettingsForm from '@/components/cascade/AdapterSettingsForm.vue'
import CascadeAdapterBadge from '@/components/cascade/CascadeAdapterBadge.vue'
import { cascadeService } from '@/api/services/cascade.service'
import { ratesService } from '@/api/services/rates.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import type {
  CascadeAdapterInfo,
  CascadeProvider,
  CascadeProviderTestIssueResult,
  CascadeRateSource,
  PaymentMethod,
  RateConfig,
} from '@/types'

const toast = useToast()
const { confirm } = useConfirm()

const paymentMethods: { value: PaymentMethod; label: string }[] = [
  { value: 'sbp', label: 'СБП' },
  { value: 'card', label: 'Карта' },
  { value: 'sim', label: 'SIM' },
]

const loading = ref(false)
const saving = ref(false)
const adjusting = ref(false)
const search = ref('')
const providers = ref<CascadeProvider[]>([])
const adapters = ref<CascadeAdapterInfo[]>([])
const rateConfigs = ref<RateConfig[]>([])

const showModal = ref(false)
const editing = ref<CascadeProvider | null>(null)
const activeTab = ref('general')

// "Тестовая выдача" — a probe modal that hits the provider's live API
// and shows the response. Independent of the edit modal so operators can
// keep both open side-by-side when comparing config vs runtime.
const showTestIssue = ref(false)
const testIssueProvider = ref<CascadeProvider | null>(null)
const testIssueLoading = ref(false)
const testIssueResult = ref<CascadeProviderTestIssueResult | null>(null)
const testIssueForm = reactive<{
  amount: number
  payment_method: PaymentMethod
  payment_option_code: string
  auto_cancel: boolean
}>({
  amount: 1000,
  payment_method: 'sbp' as PaymentMethod,
  payment_option_code: '',
  auto_cancel: true,
})
const testIssueMethodOptions = computed(() =>
  paymentMethods.map((m) => ({ value: m.value, label: m.label })),
)

const tabs = computed(() => {
  const base = [
    { key: 'general', label: 'Основные' },
    { key: 'credentials', label: 'Аутентификация' },
    { key: 'fees', label: 'Комиссии' },
    { key: 'rates', label: 'Курс' },
    { key: 'limits', label: 'Лимиты' },
    { key: 'breaker', label: 'Circuit breaker' },
    { key: 'settings', label: 'Настройки адаптера' },
  ]
  if (editing.value) base.push({ key: 'balance', label: 'Баланс' })
  return base
})

interface FormState {
  code: string
  name: string
  adapter_type: string
  base_url: string
  is_active: boolean
  api_key: string
  api_secret: string
  webhook_secret: string
  initial_balance_usdt: number
  fees: Record<string, number>
  rate_source: CascadeRateSource
  rate_config_id: number | null
  min_amount_fiat: number | undefined
  max_amount_fiat: number | undefined
  cb_window_seconds: number
  cb_threshold_failures: number
  cb_threshold_rate: number
  cb_cooldown_seconds: number
  request_timeout_ms: number
  cancel_timeout_ms: number
  priority_weight: number
  settings: Record<string, unknown>
}

const form = reactive<FormState>(emptyForm())

const balanceForm = reactive({ delta: 0, reason: 'manual_adjustment' })

const upstreamLoading = ref(false)
const upstreamBalance = ref<number | null>(null)
const upstreamSupported = ref<boolean | null>(null)

async function refreshUpstream() {
  if (!editing.value) return
  upstreamLoading.value = true
  try {
    const { data } = await cascadeService.upstreamBalance(editing.value.id)
    upstreamBalance.value = data.balance_usdt
    upstreamSupported.value = data.supported
  } catch (e: any) {
    toast.error(e?.response?.data?.error?.message || 'Не удалось получить баланс')
    upstreamSupported.value = false
  } finally {
    upstreamLoading.value = false
  }
}

const adapterOptions = computed(() =>
  adapters.value.map((a) => ({ value: a.code, label: `${a.display_name} · ${a.code}` })),
)

const selectedAdapter = computed<CascadeAdapterInfo | undefined>(() =>
  adapters.value.find((a) => a.code === form.adapter_type),
)

// Bridge between AdapterSettingsForm's ``Record<string, unknown>`` shape
// and the flat ``form.api_key/api_secret/webhook_secret`` slots that the
// rest of the form (and the submit payload builder) already uses. The
// computed proxies so we don't have to teach AdapterSettingsForm about
// dotted paths or about which three keys are encrypted columns.
const credentialsModel = computed<Record<string, unknown>>({
  get: () => ({
    api_key: form.api_key,
    api_secret: form.api_secret,
    webhook_secret: form.webhook_secret,
  }),
  set: (v) => {
    form.api_key = String(v.api_key ?? '')
    form.api_secret = String(v.api_secret ?? '')
    form.webhook_secret = String(v.webhook_secret ?? '')
  },
})

const rateConfigOptions = computed(() =>
  rateConfigs.value.map((r) => ({
    value: r.id,
    label: `${r.name} · ${r.fiat_currency} · ${r.current_rate ?? '–'}${r.is_active ? '' : ' (off)'}`,
  })),
)

const filtered = computed(() => {
  const t = search.value.trim().toLowerCase()
  if (!t) return providers.value
  return providers.value.filter(
    (p) => p.code.toLowerCase().includes(t) || p.name.toLowerCase().includes(t),
  )
})

function emptyForm(): FormState {
  return {
    code: '',
    name: '',
    adapter_type: '',
    base_url: '',
    is_active: true,
    api_key: '',
    api_secret: '',
    webhook_secret: '',
    initial_balance_usdt: 0,
    fees: {},
    rate_source: 'provider',
    rate_config_id: null,
    min_amount_fiat: undefined,
    max_amount_fiat: undefined,
    cb_window_seconds: 300,
    cb_threshold_failures: 5,
    cb_threshold_rate: 0.5,
    cb_cooldown_seconds: 600,
    request_timeout_ms: 3000,
    cancel_timeout_ms: 2000,
    priority_weight: 100,
    settings: {},
  }
}

function resetForm() {
  Object.assign(form, emptyForm())
  if (!form.adapter_type && adapters.value.length) {
    form.adapter_type = adapters.value[0].code
  }
  applyAdapterDefaults()
  activeTab.value = 'general'
}

function applyAdapterDefaults() {
  // Pre-fill the settings form with the adapter's declared defaults.
  if (!selectedAdapter.value) return
  for (const field of selectedAdapter.value.settings_schema) {
    if (form.settings[field.key] !== undefined) continue
    if (field.default !== undefined && field.default !== null) {
      form.settings[field.key] = field.default
    }
  }
  // If the adapter cannot quote its own rate, force 'platform' mode.
  if (!selectedAdapter.value.supports_provider_rate) {
    form.rate_source = 'platform'
  }
}

watch(
  () => form.adapter_type,
  (newType, oldType) => {
    if (!oldType || newType === oldType) return
    // Changing adapter resets settings — different adapters speak different schemas.
    form.settings = {}
    applyAdapterDefaults()
  },
)

// Used by CascadeAdapterBadge to look up the adapter (including its
// ``logo_filename``) by code in the providers list. Returns ``null`` so
// the badge falls back to the bare ``code`` while the adapters call is
// still loading.
function adapterByCode(code: string): CascadeAdapterInfo | null {
  return adapters.value.find((a) => a.code === code) ?? null
}

function updateFee(method: string, raw: unknown) {
  const num = raw === '' || raw === null ? 0 : Number(raw)
  form.fees = { ...form.fees, [method]: Number.isFinite(num) ? num : 0 }
}

async function load() {
  loading.value = true
  try {
    const [providersRes, adaptersRes, ratesRes] = await Promise.all([
      cascadeService.listProviders({ limit: 1000 }),
      cascadeService.listAdapters(),
      ratesService.list(),
    ])
    providers.value = providersRes.data
    adapters.value = adaptersRes.data
    rateConfigs.value = ratesRes.data
  } catch {
    toast.error('Ошибка загрузки провайдеров')
  } finally {
    loading.value = false
  }
}

function openCreate() {
  editing.value = null
  resetForm()
  showModal.value = true
}

function openEdit(p: CascadeProvider) {
  editing.value = p
  Object.assign(form, emptyForm())
  form.code = p.code
  form.name = p.name
  form.adapter_type = p.adapter_type
  form.base_url = p.base_url
  form.is_active = p.is_active
  form.fees = { ...(p.fees as Record<string, number>) }
  form.rate_source = p.rate_source
  form.rate_config_id = p.rate_config_id ?? null
  form.min_amount_fiat = p.min_amount_fiat ?? undefined
  form.max_amount_fiat = p.max_amount_fiat ?? undefined
  form.cb_window_seconds = p.cb_window_seconds
  form.cb_threshold_failures = p.cb_threshold_failures
  form.cb_threshold_rate = p.cb_threshold_rate
  form.cb_cooldown_seconds = p.cb_cooldown_seconds
  form.request_timeout_ms = p.request_timeout_ms
  form.cancel_timeout_ms = p.cancel_timeout_ms
  form.priority_weight = p.priority_weight
  form.settings = { ...((p.settings as Record<string, unknown>) ?? {}) }
  applyAdapterDefaults()
  balanceForm.delta = 0
  balanceForm.reason = 'manual_adjustment'
  upstreamBalance.value = null
  upstreamSupported.value = null
  activeTab.value = 'general'
  showModal.value = true
  // Pre-fetch upstream balance lazily so the Balance tab is ready when the user opens it.
  void refreshUpstream()
}

async function save() {
  if (!editing.value && !form.code.trim()) {
    toast.error('Укажите код провайдера')
    return
  }
  if (!form.adapter_type) {
    toast.error('Выберите тип интеграции')
    return
  }
  if (form.rate_source === 'platform' && !form.rate_config_id) {
    toast.error('Выберите RateConfig для нашего курса')
    return
  }

  saving.value = true
  try {
    if (editing.value) {
      const payload: Record<string, unknown> = {
        name: form.name,
        adapter_type: form.adapter_type,
        base_url: form.base_url,
        is_active: form.is_active,
        fees: form.fees,
        rate_source: form.rate_source,
        rate_config_id: form.rate_source === 'platform' ? form.rate_config_id : null,
        settings: form.settings,
        cb_window_seconds: form.cb_window_seconds,
        cb_threshold_failures: form.cb_threshold_failures,
        cb_threshold_rate: form.cb_threshold_rate,
        cb_cooldown_seconds: form.cb_cooldown_seconds,
        request_timeout_ms: form.request_timeout_ms,
        cancel_timeout_ms: form.cancel_timeout_ms,
        priority_weight: form.priority_weight,
      }
      if (form.min_amount_fiat !== undefined) payload.min_amount_fiat = form.min_amount_fiat
      if (form.max_amount_fiat !== undefined) payload.max_amount_fiat = form.max_amount_fiat
      if (form.api_key) payload.api_key = form.api_key
      if (form.api_secret) payload.api_secret = form.api_secret
      if (form.webhook_secret) payload.webhook_secret = form.webhook_secret
      await cascadeService.updateProvider(editing.value.id, payload as any)
    } else {
      await cascadeService.createProvider({
        code: form.code.trim(),
        name: form.name,
        adapter_type: form.adapter_type,
        base_url: form.base_url,
        is_active: form.is_active,
        api_key: form.api_key || undefined,
        api_secret: form.api_secret || undefined,
        webhook_secret: form.webhook_secret || undefined,
        initial_balance_usdt: form.initial_balance_usdt || 0,
        fees: form.fees,
        rate_source: form.rate_source,
        rate_config_id:
          form.rate_source === 'platform' ? form.rate_config_id ?? undefined : undefined,
        settings: form.settings,
        min_amount_fiat: form.min_amount_fiat,
        max_amount_fiat: form.max_amount_fiat,
        cb_window_seconds: form.cb_window_seconds,
        cb_threshold_failures: form.cb_threshold_failures,
        cb_threshold_rate: form.cb_threshold_rate,
        cb_cooldown_seconds: form.cb_cooldown_seconds,
        request_timeout_ms: form.request_timeout_ms,
        cancel_timeout_ms: form.cancel_timeout_ms,
        priority_weight: form.priority_weight,
      })
    }
    toast.success('Сохранено')
    showModal.value = false
    load()
  } catch (e: any) {
    toast.error(e?.response?.data?.error?.message || 'Не удалось сохранить провайдера')
  } finally {
    saving.value = false
  }
}

async function adjustBalance() {
  if (!editing.value) return
  if (!balanceForm.delta) {
    toast.error('Укажите дельту')
    return
  }
  adjusting.value = true
  try {
    const { data } = await cascadeService.adjustBalance(
      editing.value.id,
      balanceForm.delta,
      balanceForm.reason || 'manual_adjustment',
    )
    toast.success(`Баланс обновлён: ${data.new_balance_usdt} USDT`)
    balanceForm.delta = 0
  } catch (e: any) {
    toast.error(e?.response?.data?.error?.message || 'Не удалось обновить баланс')
  } finally {
    adjusting.value = false
  }
}

function openTestIssue(p: CascadeProvider) {
  // Reset the form to safe defaults for this provider so two consecutive
  // tests against different rows don't carry stale state. Don't clobber
  // ``amount`` between sessions — admins usually probe with the same
  // round-number repeatedly.
  testIssueProvider.value = p
  testIssueResult.value = null
  testIssueForm.payment_option_code = ''
  testIssueForm.auto_cancel = true
  showTestIssue.value = true
}

async function runTestIssue() {
  if (!testIssueProvider.value) return
  if (!testIssueForm.amount || testIssueForm.amount <= 0) {
    toast.error('Укажите сумму')
    return
  }
  testIssueLoading.value = true
  testIssueResult.value = null
  try {
    const { data } = await cascadeService.testIssue(testIssueProvider.value.id, {
      amount: testIssueForm.amount,
      payment_method: testIssueForm.payment_method,
      payment_option_code: testIssueForm.payment_option_code || undefined,
      auto_cancel: testIssueForm.auto_cancel,
    })
    testIssueResult.value = data
    if (data.ok) {
      toast.success(`Провайдер выдал реквизит: ${data.requisite?.bank_name ?? ''}`)
    } else {
      toast.error(`${data.outcome}: ${data.summary}`)
    }
  } catch (e: any) {
    toast.error(e?.response?.data?.error?.message || 'Не удалось выполнить тест')
  } finally {
    testIssueLoading.value = false
  }
}

async function askDelete(p: CascadeProvider) {
  const ok = await confirm({
    title: 'Удалить провайдера?',
    message: `Провайдер «${p.name}» будет удалён или деактивирован, если есть история ордеров.`,
    confirmText: 'Удалить',
    cancelText: 'Отмена',
    variant: 'danger',
  })
  if (!ok) return
  try {
    await cascadeService.deleteProvider(p.id)
    toast.success('Провайдер удалён')
    showModal.value = false
    load()
  } catch {
    toast.error('Не удалось удалить провайдера')
  }
}

watch(showModal, (val) => {
  if (!val) editing.value = null
})

onMounted(load)
</script>
