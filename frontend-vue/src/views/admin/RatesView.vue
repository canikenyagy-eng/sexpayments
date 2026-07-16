<template>
  <div>
    <PageHeader title="Курсы">
      <template #actions>
        <BaseButton variant="gold" @click="openCreate">+ Создать</BaseButton>
      </template>
    </PageHeader>

    <DataTable
      :columns="columns"
      :rows="items"
      :loading="loading"
      row-key="id"
    >
      <template #cell-is_active="{ value }">
        <StatusBadge :status="String(value)" />
      </template>
      <template #cell-current_rate="{ value }">
        <span class="font-mono font-bold text-accent">{{ value != null ? value.toFixed(4) : '—' }}</span>
      </template>
      <template #cell-source="{ value }">
        <RateSourceBadge :source="value as string | null" />
      </template>
      <template #cell-side="{ value }">
        <span
          class="inline-flex items-center rounded-lg border px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider"
          :class="value === 'buy'
            ? 'border-status-success/40 bg-status-success/10 text-status-success'
            : 'border-status-danger/40 bg-status-danger/10 text-status-danger'"
        >
          {{ (value ?? '').toString().toUpperCase() }}
        </span>
      </template>
      <template #cell-last_updated_at="{ value }">
        {{ value ? formatDate(value) : '—' }}
      </template>
      <template #actions="{ row }">
        <div class="flex gap-1">
          <BaseButton action="refresh" variant="ghost" size="sm" @click="syncRate(row as RateConfig)" />
          <BaseButton action="edit" variant="ghost" size="sm" @click="openEdit(row as RateConfig)" />
          <BaseButton action="delete" variant="ghost" size="sm" @click="removeRate(row as RateConfig)" />
        </div>
      </template>
    </DataTable>

    <!-- Create/Edit modal -->
    <BaseModal v-model="showModal" :title="editing ? 'Редактирование курса' : 'Новый курс'">
      <div class="space-y-4">
        <BaseInput v-model="form.name" label="Название" required />
        <BaseSelect
          v-model="form.source"
          label="Источник"
          :options="rateSourceOptions"
          :disabled="!!editing"
        />
        <BaseSelect v-model="form.side" label="Сторона" :options="[{ value: 'buy', label: 'Buy' }, { value: 'sell', label: 'Sell' }]" />
        <BaseInput v-model="form.position" label="Позиция в стакане" type="number" />
        <BaseSelect v-model="form.fiat_currency" label="Фиат" :options="[{ value: 'RUB', label: 'RUB' }, { value: 'AZN', label: 'AZN' }]" />
        <BaseInput v-model="form.update_interval_seconds" label="Интервал обновления (сек)" type="number" />
        <BaseSelect v-model="form.is_active" label="Активен" :options="[{ value: 'true', label: 'Да' }, { value: 'false', label: 'Нет' }]" />
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showModal = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="saving" @click="save">Сохранить</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import DataTable from '@/components/ui/DataTable.vue'
import type { Column } from '@/components/ui/DataTable.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import RateSourceBadge from '@/components/ui/RateSourceBadge.vue'
import { ratesService } from '@/api/services/rates.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import { formatDate } from '@/utils/format'
import { rateSourceOptions } from '@/constants/options'
import type { RateConfig } from '@/types'

const toast = useToast()
const { confirm } = useConfirm()

const loading = ref(false)
const saving = ref(false)
const items = ref<RateConfig[]>([])
const showModal = ref(false)
const editing = ref<RateConfig | null>(null)

const form = reactive({
  name: '',
  source: 'bybit',
  side: 'buy',
  position: '1',
  fiat_currency: 'RUB',
  update_interval_seconds: '60',
  is_active: 'true',
})

const columns: Column[] = [
  { key: 'id', label: 'ID' },
  { key: 'name', label: 'Название' },
  { key: 'source', label: 'Источник' },
  { key: 'side', label: 'Сторона' },
  { key: 'position', label: 'Позиция' },
  { key: 'fiat_currency', label: 'Фиат' },
  { key: 'current_rate', label: 'Текущий курс', align: 'right' },
  { key: 'is_active', label: 'Статус' },
  { key: 'last_updated_at', label: 'Обновлён' },
]

async function load() {
  loading.value = true
  try {
    const { data } = await ratesService.list()
    items.value = data
  } catch { toast.error('Ошибка загрузки') }
  finally { loading.value = false }
}

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', source: 'bybit', side: 'buy', position: '1', fiat_currency: 'RUB', update_interval_seconds: '60', is_active: 'true' })
  showModal.value = true
}

function openEdit(row: RateConfig) {
  editing.value = row
  Object.assign(form, {
    name: row.name,
    source: row.source,
    side: row.side,
    position: String(row.position),
    fiat_currency: row.fiat_currency,
    update_interval_seconds: String(row.update_interval_seconds),
    is_active: String(row.is_active),
  })
  showModal.value = true
}

async function save() {
  saving.value = true
  try {
    const payload: any = {
      name: form.name,
      side: form.side,
      position: Number(form.position),
      fiat_currency: form.fiat_currency,
      update_interval_seconds: Number(form.update_interval_seconds),
      is_active: form.is_active === 'true',
    }
    if (!editing.value) {
      payload.source = form.source
    }
    if (editing.value) {
      await ratesService.update(editing.value.id, payload)
      toast.success('Курс обновлён')
    } else {
      await ratesService.create(payload)
      toast.success('Курс создан')
    }
    showModal.value = false
    load()
  } catch { toast.error('Ошибка сохранения') }
  finally { saving.value = false }
}

async function syncRate(row: RateConfig) {
  try {
    await ratesService.sync(row.id)
    toast.success('Курс синхронизирован')
    load()
  } catch { toast.error('Ошибка синхронизации') }
}

async function removeRate(row: RateConfig) {
  if (!(await confirm(`Удалить курс "${row.name}"?`))) return
  try {
    await ratesService.remove(row.id)
    toast.success('Курс удалён')
    load()
  } catch { toast.error('Ошибка удаления') }
}

onMounted(load)
</script>
