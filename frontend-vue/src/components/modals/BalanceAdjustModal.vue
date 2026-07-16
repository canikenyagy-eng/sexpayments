<template>
  <BaseModal :model-value="open" title="Корректировка баланса" size="md" @update:model-value="close">
    <form class="space-y-4" @submit.prevent="submit">
      <div v-if="canPickType" class="flex gap-1 rounded-xl bg-bg-surface p-1">
        <button
          v-for="type in allowedTypes"
          :key="type.value"
          type="button"
          class="flex-1 rounded-lg py-2 text-sm font-bold transition"
          :class="form.entityType === type.value ? 'bg-accent/15 text-accent' : 'text-text-muted hover:text-text-main'"
          @click="switchEntityType(type.value)"
        >
          {{ type.label }}
        </button>
      </div>

      <template v-if="form.entityType === 'merchant'">
        <BaseInput
          v-model="form.entityIdInput"
          label="Merchant ID"
          placeholder="ID мерчанта"
          type="number"
          required
        />
      </template>
      <template v-else>
        <UserAutocomplete
          ref="autocompleteRef"
          v-model="form.userId"
          :role="autocompleteRole"
          :label="userInputLabel"
          placeholder="ID или логин"
          hint="Начните вводить ID или логин для поиска"
        />
      </template>

      <div v-if="hasEntitySelected" class="rounded-xl border border-border bg-bg-surface p-3">
        <div class="mb-2 flex items-center justify-between">
          <span class="text-xs font-bold uppercase tracking-wider text-text-muted">
            Текущий баланс
          </span>
          <span v-if="balancesLoading" class="text-xs text-text-muted">Загрузка…</span>
        </div>
        <div v-if="!balancesLoading && entityBalances.length === 0" class="text-xs text-text-muted">
          Балансов нет
        </div>
        <div v-else-if="!balancesLoading" class="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
          <div
            v-for="b in entityBalances"
            :key="`${b.type}-${b.currency}`"
            class="flex items-center justify-between rounded-lg bg-bg-card px-2.5 py-1.5 text-xs"
            :class="balanceRowClass(b)"
          >
            <span class="font-bold uppercase tracking-wider text-text-muted">
              {{ b.type }}
            </span>
            <span class="font-bold text-text-main">
              {{ formatBalanceAmount(b.amount) }} {{ b.currency }}
            </span>
          </div>
        </div>
      </div>

      <div class="flex gap-2">
        <button
          type="button"
          class="flex flex-1 items-center justify-center gap-2 rounded-xl border-2 py-3 text-sm font-bold transition"
          :class="form.operation === 'credit'
            ? 'border-status-success bg-status-success/10 text-status-success'
            : 'border-border text-text-muted hover:border-status-success/50 hover:text-status-success'"
          @click="form.operation = 'credit'"
        >
          <Plus class="h-4 w-4" />
          Пополнить
        </button>
        <button
          type="button"
          class="flex flex-1 items-center justify-center gap-2 rounded-xl border-2 py-3 text-sm font-bold transition"
          :class="form.operation === 'debit'
            ? 'border-status-danger bg-status-danger/10 text-status-danger'
            : 'border-border text-text-muted hover:border-status-danger/50 hover:text-status-danger'"
          @click="form.operation = 'debit'"
        >
          <Minus class="h-4 w-4" />
          Списать
        </button>
      </div>

      <BaseInput
        v-model="form.amount"
        label="Сумма"
        placeholder="0.00"
        type="number"
        required
      />

      <div class="grid grid-cols-2 gap-3">
        <BaseSelect
          v-model="form.currency"
          label="Валюта"
          :options="currencyOptions"
        />
        <BaseSelect
          v-model="form.balanceType"
          label="Тип баланса"
          :options="balanceTypeOptions"
        />
      </div>

      <div>
        <label class="mb-1 block text-sm font-medium text-text-secondary">
          Причина <span class="text-status-danger">*</span>
        </label>
        <textarea
          v-model="form.reason"
          rows="2"
          required
          placeholder="Опишите причину корректировки..."
          class="w-full rounded-xl border border-border bg-bg-card px-3 py-2 text-sm text-text-main placeholder-text-muted outline-none transition focus:border-accent"
        />
      </div>

      <div
        v-if="hasPreview"
        class="rounded-xl p-3 text-sm"
        :class="form.operation === 'credit' ? 'bg-status-success/10 text-status-success' : 'bg-status-danger/10 text-status-danger'"
      >
        <span class="font-bold">{{ form.operation === 'credit' ? '+' : '−' }}{{ form.amount }} {{ form.currency }}</span>
        <span class="ml-2 text-text-muted">
          → {{ entityTypeLabel }} #{{ resolvedEntityId }}, {{ form.balanceType }} баланс
        </span>
      </div>

      <div class="flex justify-end gap-2 pt-1">
        <BaseButton type="button" variant="dark" @click="close">Отмена</BaseButton>
        <BaseButton
          type="submit"
          :variant="form.operation === 'credit' ? 'success' : 'danger'"
          :loading="loading"
        >
          {{ form.operation === 'credit' ? 'Пополнить' : 'Списать' }}
        </BaseButton>
      </div>
    </form>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, reactive, ref, watch, nextTick } from 'vue'
import { Plus, Minus } from 'lucide-vue-next'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import UserAutocomplete from '@/components/ui/UserAutocomplete.vue'
import { financesService } from '@/api/services/finances.service'
import { useToast } from '@/composables/useToast'
import type { AdminAdjustRequest, BalanceInfo, UserRole } from '@/types'

type EntityType = 'trader' | 'merchant' | 'teamlead'

const props = withDefaults(defineProps<{
  open: boolean
  initialUser?: { id: number; username: string; role: UserRole } | null
  initialMerchantId?: number | null
  allowedTypes?: Array<{ value: EntityType; label: string }>
  lockEntityType?: boolean
}>(), {
  allowedTypes: () => [
    { value: 'trader', label: 'Трейдер' },
    { value: 'merchant', label: 'Мерчант' },
    { value: 'teamlead', label: 'Тимлид' },
  ],
  lockEntityType: false,
  initialUser: null,
  initialMerchantId: null,
})

const emit = defineEmits<{
  'update:open': [value: boolean]
  'success': []
}>()

const toast = useToast()
const loading = ref(false)
const autocompleteRef = ref<InstanceType<typeof UserAutocomplete> | null>(null)

const form = reactive({
  entityType: 'trader' as EntityType,
  userId: null as number | null,
  entityIdInput: '',
  operation: 'credit' as 'credit' | 'debit',
  amount: '',
  currency: 'USDT',
  balanceType: 'work',
  reason: '',
})

const currencyOptions = [
  { value: 'USDT', label: 'USDT' },
  { value: 'RUB', label: 'RUB' },
]

const balanceTypeOptions = [
  { value: 'work', label: 'WORK (рабочий)' },
  { value: 'escrow', label: 'ESCROW (заморозка)' },
  { value: 'safe_deposit', label: 'SAFE_DEPOSIT (страховой)' },
]

const canPickType = computed(() => !props.lockEntityType && props.allowedTypes.length > 1)

const autocompleteRole = computed<UserRole | undefined>(() => {
  if (form.entityType === 'trader') return 'trader'
  if (form.entityType === 'teamlead') return 'teamlead'
  return undefined
})

const userInputLabel = computed(() => {
  if (form.entityType === 'trader') return 'Трейдер (ID или логин)'
  if (form.entityType === 'teamlead') return 'Тимлид (ID или логин)'
  return 'Пользователь (ID или логин)'
})

const entityTypeLabel = computed(() => {
  switch (form.entityType) {
    case 'trader': return 'Трейдер'
    case 'merchant': return 'Мерчант'
    case 'teamlead': return 'Тимлид'
    default: return ''
  }
})

const resolvedEntityId = computed(() => {
  if (form.entityType === 'merchant') return form.entityIdInput
  return form.userId ?? ''
})

const hasPreview = computed(() => {
  if (!form.amount) return false
  return form.entityType === 'merchant'
    ? Boolean(form.entityIdInput)
    : Boolean(form.userId)
})

const resolvedMerchantId = computed(() => {
  if (form.entityType !== 'merchant') return null
  const n = parseInt(form.entityIdInput)
  return Number.isNaN(n) || n <= 0 ? null : n
})

const hasEntitySelected = computed(() => {
  return form.entityType === 'merchant'
    ? resolvedMerchantId.value !== null
    : form.userId !== null
})

const entityBalances = ref<BalanceInfo[]>([])
const balancesLoading = ref(false)
let balanceFetchToken = 0
let balanceFetchTimer: ReturnType<typeof setTimeout> | null = null

function formatBalanceAmount(amount: number): string {
  return amount.toLocaleString('ru-RU', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function balanceRowClass(b: BalanceInfo): string {
  const matchesSelection =
    b.type.toLowerCase() === form.balanceType.toLowerCase() &&
    b.currency.toLowerCase() === form.currency.toLowerCase()
  return matchesSelection ? 'ring-1 ring-accent/60' : ''
}

async function fetchEntityBalances() {
  const token = ++balanceFetchToken
  if (!hasEntitySelected.value) {
    entityBalances.value = []
    balancesLoading.value = false
    return
  }
  balancesLoading.value = true
  try {
    const params: { user_id?: number; merchant_id?: number } = {}
    if (form.entityType === 'merchant') {
      if (resolvedMerchantId.value !== null) params.merchant_id = resolvedMerchantId.value
    } else if (form.userId !== null) {
      params.user_id = form.userId
    }
    const { data } = await financesService.listBalances(params)
    if (token === balanceFetchToken) {
      entityBalances.value = data
    }
  } catch {
    if (token === balanceFetchToken) {
      entityBalances.value = []
    }
  } finally {
    if (token === balanceFetchToken) {
      balancesLoading.value = false
    }
  }
}

function scheduleBalanceFetch(debounceMs = 0) {
  if (balanceFetchTimer) clearTimeout(balanceFetchTimer)
  if (debounceMs <= 0) {
    fetchEntityBalances()
    return
  }
  balanceFetchTimer = setTimeout(fetchEntityBalances, debounceMs)
}

watch(() => form.userId, () => scheduleBalanceFetch())
watch(() => resolvedMerchantId.value, () => scheduleBalanceFetch(300))
watch(() => form.entityType, () => {
  entityBalances.value = []
  scheduleBalanceFetch()
})

function resetForm() {
  form.entityType = props.allowedTypes[0]?.value ?? 'trader'
  form.userId = null
  form.entityIdInput = ''
  form.operation = 'credit'
  form.amount = ''
  form.currency = 'USDT'
  form.balanceType = 'work'
  form.reason = ''
  entityBalances.value = []
  balancesLoading.value = false
  if (balanceFetchTimer) {
    clearTimeout(balanceFetchTimer)
    balanceFetchTimer = null
  }
  balanceFetchToken++
}

function applyInitial() {
  if (props.initialUser) {
    const role = props.initialUser.role
    if (role === 'merchant') {
      form.entityType = 'merchant'
      form.entityIdInput = ''
    } else if (role === 'trader' || role === 'teamlead') {
      form.entityType = role
    }
    nextTick(() => {
      if (form.entityType !== 'merchant' && props.initialUser) {
        autocompleteRef.value?.setInitial({ id: props.initialUser.id, username: props.initialUser.username })
      }
    })
  } else if (props.initialMerchantId != null) {
    form.entityType = 'merchant'
    form.entityIdInput = String(props.initialMerchantId)
  }
}

function switchEntityType(type: EntityType) {
  form.entityType = type
  form.userId = null
  form.entityIdInput = ''
  nextTick(() => autocompleteRef.value?.reset())
}

function close() {
  emit('update:open', false)
}

watch(() => props.open, (val) => {
  if (val) {
    resetForm()
    applyInitial()
    // applyInitial() may seed userId (via autocomplete ref) on the next tick,
    // so kick off a fetch after Vue settles the initial user/merchant values.
    nextTick(() => scheduleBalanceFetch())
  }
})

async function submit() {
  const rawAmount = parseFloat(form.amount)
  if (Number.isNaN(rawAmount) || rawAmount <= 0) {
    toast.error('Введите корректную сумму')
    return
  }
  if (!form.reason.trim()) {
    toast.error('Укажите причину')
    return
  }

  const payload: AdminAdjustRequest = {
    amount: form.operation === 'credit' ? rawAmount : -rawAmount,
    currency: form.currency as AdminAdjustRequest['currency'],
    balance_type: form.balanceType as AdminAdjustRequest['balance_type'],
    reason: form.reason.trim(),
  }

  if (form.entityType === 'merchant') {
    const mid = parseInt(form.entityIdInput)
    if (Number.isNaN(mid) || mid <= 0) {
      toast.error('Введите ID мерчанта')
      return
    }
    payload.merchant_id = mid
  } else {
    if (!form.userId) {
      toast.error(`Выберите ${entityTypeLabel.value.toLowerCase()}а`)
      return
    }
    payload.user_id = form.userId
  }

  loading.value = true
  try {
    const { data } = await financesService.adminAdjust(payload)
    toast.success(
      `${form.operation === 'credit' ? 'Пополнено' : 'Списано'}: ${rawAmount} ${form.currency} → ${data.entity}`,
    )
    emit('success')
    close()
  } catch (e: any) {
    toast.error(e?.response?.data?.detail ?? 'Ошибка корректировки')
  } finally {
    loading.value = false
  }
}
</script>
