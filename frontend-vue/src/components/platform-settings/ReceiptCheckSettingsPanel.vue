<template>
  <div class="space-y-6">
    <!-- All providers (admin can keep inactive fallbacks configured) -->
    <div>
      <div class="mb-3 flex items-center justify-between">
        <h3 class="text-base font-bold text-text-main">Все провайдеры</h3>
        <BaseButton variant="gold" size="sm" @click="openCreate">+ Добавить</BaseButton>
      </div>

      <DataTable
        :columns="providerColumns"
        :rows="providers"
        :loading="loadingList"
        row-key="id"
        clickable
        empty-text="Подключите первого провайдера, чтобы трейдеры могли проверять чеки"
        @row-click="(row: Record<string, any>) => openEdit(row as ReceiptCheckProvider)"
      >
        <template #cell-name="{ row }">
          <div class="flex flex-col">
            <span class="font-bold text-text-main">{{ (row as ReceiptCheckProvider).name }}</span>
            <span class="text-xs text-text-muted">{{ (row as ReceiptCheckProvider).base_url }}</span>
          </div>
        </template>

        <template #cell-code="{ row }">
          <span class="rounded-full bg-bg-hover px-2 py-0.5 text-xs text-text-muted">
            {{ (row as ReceiptCheckProvider).code }}
          </span>
        </template>

        <template #cell-adapter_type="{ row }">
          <span class="rounded-full bg-bg-hover px-2 py-0.5 text-xs text-text-muted">
            {{ (row as ReceiptCheckProvider).adapter_type }}
          </span>
        </template>

        <template #cell-api_key_masked="{ row }">
          <span class="font-mono text-xs">{{ (row as ReceiptCheckProvider).api_key_masked ?? '—' }}</span>
        </template>

        <template #cell-price_usdt="{ row }">
          <span class="font-bold text-text-main">
            {{ Number((row as ReceiptCheckProvider).price_usdt).toFixed(2) }} USDT
          </span>
        </template>

        <template #cell-is_active="{ row }">
          <span
            v-if="(row as ReceiptCheckProvider).is_active"
            class="rounded-full bg-status-success/10 px-2 py-0.5 text-xs text-status-success"
          >активен</span>
          <span
            v-else
            class="rounded-full bg-bg-hover px-2 py-0.5 text-xs text-text-muted"
          >отключён</span>
        </template>

        <template #actions="{ row }">
          <BaseButton action="edit" variant="ghost" size="sm" @click="openEdit(row as ReceiptCheckProvider)" />
          <BaseButton action="delete" variant="ghost" size="sm" @click="remove(row as ReceiptCheckProvider)" />
        </template>
      </DataTable>
    </div>

    <!-- Provider edit/create modal -->
    <BaseModal v-model="showModal" :title="modalTitle" size="md">
      <div class="space-y-4">
        <!-- Remote balance, shown only when editing an existing provider.
             Pulled lazily when the modal opens so we don't burn the provider's
             quota on every page render. -->
        <div v-if="editingId" class="space-y-2">
          <div class="flex items-center justify-between">
            <span class="text-xs uppercase tracking-wider text-text-muted">Баланс у провайдера</span>
            <BaseButton
              variant="ghost"
              size="sm"
              :loading="balanceLoading"
              @click="refreshBalance(editingId)"
            >
              Обновить
            </BaseButton>
          </div>
          <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatCard
              label="Остаток"
              :value="formatNumber(balance?.remaining)"
              :subtitle="balance?.error ?? undefined"
            />
            <StatCard label="Куплено" :value="formatNumber(balance?.own)" />
            <StatCard label="Подарено" :value="formatNumber(balance?.gifted)" />
            <StatCard label="Всего" :value="formatNumber(balance?.total_checks)" />
          </div>
        </div>

        <div v-if="!editingId" class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <BaseInput v-model="form.code" label="Код (для CLI/логов)" placeholder="trexo" required />
          <BaseInput v-model="form.name" label="Название" placeholder="TREXO" required />
        </div>
        <BaseSelect
          v-if="!editingId"
          v-model="form.adapter_type"
          label="Тип адаптера"
          :options="adapterOptions"
        />
        <BaseInput v-model="form.base_url" label="Базовый URL" placeholder="https://api.trexo.company" required />

        <BaseInput
          v-model="form.api_key"
          label="Ключ интеграции"
          type="password"
          :placeholder="editingId ? 'Оставьте пустым, чтобы не менять' : 'sk_live_…'"
          :required="!editingId"
        />
        <p v-if="editingId" class="-mt-2 text-xs text-text-muted">
          Текущий ключ сохранён в зашифрованном виде. Введите новый, чтобы заменить.
        </p>

        <div class="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <BaseInput
            v-model.number="form.price_usdt"
            label="Цена одной проверки (USDT)"
            type="number"
            placeholder="0.50"
            required
          />
          <BaseInput
            v-model.number="form.request_timeout_ms"
            label="Таймаут запроса (мс)"
            type="number"
            placeholder="90000"
          />
        </div>

        <label class="flex cursor-pointer items-center gap-3">
          <BaseSwitch v-model="form.is_active" />
          <span class="text-sm text-text-main">Активен (доступен трейдерам для выбора)</span>
        </label>
      </div>

      <template #footer>
        <BaseButton variant="dark" @click="showModal = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="saving" @click="save">Сохранить</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref, computed } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import type { SelectOption } from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseSwitch from '@/components/ui/BaseSwitch.vue'
import StatCard from '@/components/ui/StatCard.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import { platformSettingsService } from '@/api/services/platformSettings.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import type {
  ReceiptCheckProvider,
  ReceiptCheckProviderAdapter,
  ReceiptCheckProviderBalance,
  ReceiptCheckProviderCreate,
  ReceiptCheckProviderUpdate,
} from '@/types'

const adapterOptions: SelectOption[] = [
  { value: 'trexo', label: 'TREXO' },
  { value: 'detectio', label: 'detect.io' },
]

const toast = useToast()
const { confirm } = useConfirm()

const loadingList = ref(false)
const balanceLoading = ref(false)
const saving = ref(false)

const providers = ref<ReceiptCheckProvider[]>([])
const balance = ref<ReceiptCheckProviderBalance | null>(null)

const showModal = ref(false)
const editingId = ref<number | null>(null)
const form = ref<{
  code: string
  name: string
  adapter_type: string
  base_url: string
  api_key: string
  price_usdt: number
  request_timeout_ms: number
  is_active: boolean
}>({
  code: '',
  name: '',
  adapter_type: 'trexo',
  base_url: 'https://api.trexo.company',
  api_key: '',
  price_usdt: 0,
  request_timeout_ms: 90000,
  is_active: true,
})

const modalTitle = computed(() => (editingId.value ? 'Изменить провайдера' : 'Подключить провайдера'))

const providerColumns: Column[] = [
  { key: 'name', label: 'Название' },
  { key: 'code', label: 'Код' },
  { key: 'adapter_type', label: 'Адаптер' },
  { key: 'api_key_masked', label: 'Ключ интеграции' },
  { key: 'price_usdt', label: 'Цена', align: 'right' },
  { key: 'is_active', label: 'Статус' },
]

function formatNumber(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—'
  return new Intl.NumberFormat('ru-RU').format(v)
}

async function loadList() {
  loadingList.value = true
  try {
    const r = await platformSettingsService.listReceiptCheckProviders()
    providers.value = r.data
  } catch (e) {
    toast.error('Не удалось загрузить провайдеров')
  } finally {
    loadingList.value = false
  }
}

async function refreshBalance(id: number) {
  balanceLoading.value = true
  try {
    const r = await platformSettingsService.getReceiptCheckProviderBalance(id)
    balance.value = r.data
    if (r.data.error) toast.warning(`Провайдер вернул ошибку: ${r.data.error}`)
  } catch (e) {
    toast.error('Не удалось получить баланс')
  } finally {
    balanceLoading.value = false
  }
}

function openCreate() {
  editingId.value = null
  form.value = {
    code: 'trexo',
    name: 'TREXO',
    adapter_type: 'trexo',
    base_url: 'https://api.trexo.company',
    api_key: '',
    price_usdt: 0.5,
    request_timeout_ms: 90000,
    is_active: true,
  }
  showModal.value = true
}

function openEdit(p: ReceiptCheckProvider) {
  editingId.value = p.id
  form.value = {
    code: p.code,
    name: p.name,
    adapter_type: p.adapter_type,
    base_url: p.base_url,
    api_key: '',
    price_usdt: Number(p.price_usdt),
    request_timeout_ms: p.request_timeout_ms,
    is_active: p.is_active,
  }
  balance.value = null
  showModal.value = true
  // Fire-and-forget — the modal renders even if the provider rejects us
  // (the StatCards fall back to "—" and the error lands in `balance.error`).
  void refreshBalance(p.id)
}

async function save() {
  saving.value = true
  try {
    if (editingId.value) {
      const payload: ReceiptCheckProviderUpdate = {
        name: form.value.name,
        base_url: form.value.base_url,
        price_usdt: form.value.price_usdt,
        request_timeout_ms: form.value.request_timeout_ms,
        is_active: form.value.is_active,
      }
      if (form.value.api_key) payload.api_key = form.value.api_key
      await platformSettingsService.updateReceiptCheckProvider(editingId.value, payload)
      toast.success('Сохранено')
    } else {
      const payload: ReceiptCheckProviderCreate = {
        code: form.value.code,
        name: form.value.name,
        adapter_type: form.value.adapter_type as ReceiptCheckProviderAdapter,
        base_url: form.value.base_url,
        api_key: form.value.api_key,
        price_usdt: form.value.price_usdt,
        request_timeout_ms: form.value.request_timeout_ms,
        is_active: form.value.is_active,
      }
      await platformSettingsService.createReceiptCheckProvider(payload)
      toast.success('Провайдер добавлен')
    }
    showModal.value = false
    await loadList()
  } catch (e: any) {
    const msg = e?.response?.data?.detail || e?.response?.data?.message || 'Не удалось сохранить'
    toast.error(typeof msg === 'string' ? msg : 'Не удалось сохранить')
  } finally {
    saving.value = false
  }
}

async function remove(p: ReceiptCheckProvider) {
  const ok = await confirm({
    title: 'Удалить провайдера?',
    message: `Удалить «${p.name}»? Уже выполненные проверки сохранятся в истории, но новые проверки через этого провайдера выполняться не будут.`,
    confirmText: 'Удалить',
    cancelText: 'Отмена',
    variant: 'danger',
  })
  if (!ok) return
  try {
    await platformSettingsService.deleteReceiptCheckProvider(p.id)
    toast.success('Удалено')
    await loadList()
  } catch (e) {
    toast.error('Не удалось удалить')
  }
}

onMounted(loadList)
</script>
