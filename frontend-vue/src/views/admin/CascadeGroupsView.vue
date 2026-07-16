<template>
  <div>
    <PageHeader title="Группы каскада">
      <template #actions>
        <BaseButton variant="gold" @click="openCreate">+ Создать группу</BaseButton>
      </template>
    </PageHeader>

    <div class="mb-4">
      <BaseInput
        v-model="search"
        placeholder="Поиск по названию"
        class="w-full max-w-md"
      />
    </div>

    <div v-if="loading" class="py-12 text-center text-text-muted">Загрузка…</div>

    <div
      v-else-if="filteredGroups.length"
      class="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3"
    >
      <div
        v-for="g in filteredGroups"
        :key="g.id"
        class="cursor-pointer rounded-2xl border border-border bg-bg-surface p-4 transition hover:border-accent"
        @click="openEdit(g)"
      >
        <div class="mb-1 flex items-baseline justify-between gap-2">
          <h3 class="truncate font-bold text-text-main">{{ g.name }}</h3>
          <span class="rounded-full bg-bg-default px-2 py-0.5 text-xs text-text-muted">
            tier {{ g.tier }}
          </span>
        </div>
        <p class="mb-1 text-xs text-text-muted">#{{ g.id }} · {{ g.timeout_ms }} ms</p>
        <p
          v-if="g.description"
          class="mb-3 line-clamp-2 text-sm text-text-muted"
        >
          {{ g.description }}
        </p>
        <p v-else class="mb-3 text-sm text-text-muted italic">Без описания</p>
        <div class="border-t border-border pt-3 space-y-1.5 text-sm">
          <div class="flex items-center gap-2 text-text-secondary">
            <Layers class="h-4 w-4 text-text-muted" />
            <span>{{ (g.providers?.length ?? 0) }} провайдеров</span>
          </div>
          <div class="flex items-center gap-2 text-text-secondary">
            <Store class="h-4 w-4 text-text-muted" />
            <span>{{ (g.merchants?.length ?? 0) }} мерчантов</span>
          </div>
          <div v-if="!g.is_active" class="text-xs text-status-warning">Неактивна</div>
        </div>
      </div>
    </div>

    <div v-else class="py-12 text-center text-text-muted">
      {{ search ? 'Ничего не найдено' : 'Групп пока нет — создайте первую.' }}
    </div>

    <BaseModal
      v-model="showModal"
      :title="editing ? `Группа: ${editing.name}` : 'Новая группа каскада'"
      size="lg"
    >
      <BaseTabs v-model="activeTab" :tabs="tabs">
        <template #general>
          <div class="space-y-4 pt-2">
            <BaseInput v-model="form.name" label="Название" placeholder="Например: tier-1" />
            <BaseInput v-model="form.description" label="Описание" placeholder="Необязательно" />
            <div class="grid grid-cols-2 gap-3">
              <BaseInput
                v-model.number="form.tier"
                type="number"
                label="Tier"
                placeholder="1"
                min="1"
              />
              <BaseInput
                v-model.number="form.timeout_ms"
                type="number"
                label="Timeout группы (ms)"
                placeholder="3000"
                min="100"
              />
            </div>
            <label class="flex items-center gap-2 text-sm text-text-secondary">
              <input v-model="form.is_active" type="checkbox" class="h-4 w-4 accent-accent" />
              Активна
            </label>
          </div>
        </template>

        <template #providers>
          <div class="pt-2">
            <BaseTransferList
              v-model="form.provider_ids"
              :options="providerOptions"
              available-label="Доступные"
              selected-label="В группе"
              search-placeholder="Поиск по коду или названию"
            />
          </div>
        </template>

        <template #merchants>
          <div class="pt-2">
            <BaseTransferList
              v-model="form.merchant_ids"
              :options="merchantOptions"
              available-label="Доступные"
              selected-label="В группе"
              search-placeholder="Поиск по названию или ID"
            />
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
import { computed, onMounted, reactive, ref } from 'vue'
import { Layers, Store } from 'lucide-vue-next'
import PageHeader from '@/components/layout/PageHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTabs from '@/components/ui/BaseTabs.vue'
import BaseTransferList from '@/components/ui/BaseTransferList.vue'
import { cascadeService } from '@/api/services/cascade.service'
import { merchantsService } from '@/api/services/merchants.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import type { CascadeGroup, CascadeProvider, MerchantAdmin } from '@/types'

const toast = useToast()
const { confirm } = useConfirm()

const loading = ref(false)
const saving = ref(false)
const search = ref('')
const groups = ref<CascadeGroup[]>([])
const allProviders = ref<CascadeProvider[]>([])
const allMerchants = ref<MerchantAdmin[]>([])

const showModal = ref(false)
const editing = ref<CascadeGroup | null>(null)
const activeTab = ref('general')
const tabs = [
  { key: 'general', label: 'Основные' },
  { key: 'providers', label: 'Провайдеры' },
  { key: 'merchants', label: 'Мерчанты' },
]

const form = reactive({
  name: '',
  description: '',
  tier: 1,
  timeout_ms: 3000,
  is_active: true,
  provider_ids: [] as number[],
  merchant_ids: [] as number[],
})

const filteredGroups = computed(() => {
  const t = search.value.trim().toLowerCase()
  if (!t) return groups.value
  return groups.value.filter(
    g =>
      g.name.toLowerCase().includes(t) ||
      (g.description ? g.description.toLowerCase().includes(t) : false),
  )
})

const providerOptions = computed(() =>
  allProviders.value.map(p => ({
    value: p.id,
    label: `${p.name} · ${p.code}`,
    sublabel: p.is_active ? `#${p.id}` : `#${p.id} · disabled`,
  })),
)

const merchantOptions = computed(() =>
  allMerchants.value.map(m => ({
    value: m.id,
    label: m.name || `Мерчант #${m.id}`,
    sublabel: `#${m.id}`,
  })),
)

async function load() {
  loading.value = true
  try {
    const [groupsRes, providersRes, merchantsRes] = await Promise.all([
      cascadeService.listGroups(),
      cascadeService.listProviders({ limit: 1000 }),
      merchantsService.listAll({ limit: 1000 }),
    ])
    groups.value = groupsRes.data
    allProviders.value = providersRes.data
    allMerchants.value = merchantsRes.data
  } catch {
    toast.error('Ошибка загрузки')
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.name = ''
  form.description = ''
  form.tier = 1
  form.timeout_ms = 3000
  form.is_active = true
  form.provider_ids = []
  form.merchant_ids = []
  activeTab.value = 'general'
}

function openCreate() {
  editing.value = null
  resetForm()
  showModal.value = true
}

function openEdit(g: CascadeGroup) {
  editing.value = g
  form.name = g.name
  form.description = g.description || ''
  form.tier = g.tier
  form.timeout_ms = g.timeout_ms
  form.is_active = g.is_active
  form.provider_ids = (g.providers || []).map(p => p.id)
  form.merchant_ids = (g.merchants || []).map(m => m.id)
  activeTab.value = 'general'
  showModal.value = true
}

async function syncMembership(
  groupId: number,
  current: number[],
  desired: number[],
  attach: (gid: number, id: number) => Promise<unknown>,
  detach: (gid: number, id: number) => Promise<unknown>,
) {
  const toAdd = desired.filter(id => !current.includes(id))
  const toRemove = current.filter(id => !desired.includes(id))
  await Promise.all([
    ...toAdd.map(id => attach(groupId, id)),
    ...toRemove.map(id => detach(groupId, id)),
  ])
}

async function save() {
  if (!form.name.trim()) {
    toast.error('Укажите название группы')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await cascadeService.updateGroup(editing.value.id, {
        name: form.name.trim(),
        description: form.description.trim() || null,
        tier: form.tier,
        timeout_ms: form.timeout_ms,
        is_active: form.is_active,
      })
      const currentProviders = (editing.value.providers || []).map(p => p.id)
      const currentMerchants = (editing.value.merchants || []).map(m => m.id)
      await syncMembership(
        editing.value.id,
        currentProviders,
        form.provider_ids,
        cascadeService.attachProvider,
        cascadeService.detachProvider,
      )
      await syncMembership(
        editing.value.id,
        currentMerchants,
        form.merchant_ids,
        cascadeService.attachMerchant,
        cascadeService.detachMerchant,
      )
    } else {
      await cascadeService.createGroup({
        name: form.name.trim(),
        description: form.description.trim() || undefined,
        tier: form.tier,
        timeout_ms: form.timeout_ms,
        is_active: form.is_active,
        provider_ids: form.provider_ids,
        merchant_ids: form.merchant_ids,
      })
    }
    toast.success('Сохранено')
    showModal.value = false
    load()
  } catch (e: any) {
    toast.error(e?.response?.data?.error?.message || 'Не удалось сохранить группу')
  } finally {
    saving.value = false
  }
}

async function askDelete(g: CascadeGroup) {
  const ok = await confirm({
    title: 'Удалить группу?',
    message: `Группа «${g.name}» будет удалена. Привязки провайдеров и мерчантов исчезнут.`,
    confirmText: 'Удалить',
    cancelText: 'Отмена',
    variant: 'danger',
  })
  if (!ok) return
  try {
    await cascadeService.deleteGroup(g.id)
    toast.success('Группа удалена')
    showModal.value = false
    load()
  } catch {
    toast.error('Не удалось удалить группу')
  }
}

onMounted(load)
</script>
