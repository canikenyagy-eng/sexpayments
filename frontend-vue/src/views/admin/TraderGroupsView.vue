<template>
  <div>
    <PageHeader title="Группы трейдеров">
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
        <div class="mb-1 min-w-0">
          <h3 class="truncate font-bold text-text-main">{{ g.name }}</h3>
          <p class="text-xs text-text-muted">#{{ g.id }}</p>
        </div>
        <p
          v-if="g.description"
          class="mb-3 line-clamp-2 text-sm text-text-muted"
        >
          {{ g.description }}
        </p>
        <p v-else class="mb-3 text-sm text-text-muted italic">
          Без описания
        </p>
        <div class="border-t border-border pt-3 space-y-1.5 text-sm">
          <div class="flex items-center gap-2 text-text-secondary">
            <UserRound class="h-4 w-4 text-text-muted" />
            <span>{{ (g.traders?.length ?? 0) }} трейдеров</span>
          </div>
          <div class="flex items-center gap-2 text-text-secondary">
            <Store class="h-4 w-4 text-text-muted" />
            <span>{{ (g.merchants?.length ?? 0) }} мерчантов</span>
          </div>
        </div>
      </div>
    </div>

    <div v-else class="py-12 text-center text-text-muted">
      {{ search ? 'Ничего не найдено' : 'Групп пока нет — создайте первую.' }}
    </div>

    <!-- Create/edit modal -->
    <BaseModal
      v-model="showModal"
      :title="editing ? `Группа: ${editing.name}` : 'Новая группа'"
      size="lg"
    >
      <BaseTabs v-model="activeTab" :tabs="tabs">
        <template #general>
          <div class="space-y-4 pt-2">
            <BaseInput v-model="form.name" label="Название" placeholder="Например: VIP-merchants" />
            <BaseInput v-model="form.description" label="Описание" placeholder="Необязательно" />
          </div>
        </template>

        <template #traders>
          <div class="pt-2">
            <BaseTransferList
              v-model="form.trader_ids"
              :options="traderOptions"
              available-label="Доступные"
              selected-label="В группе"
              search-placeholder="Поиск по логину или ID"
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
import { Store, UserRound } from 'lucide-vue-next'
import PageHeader from '@/components/layout/PageHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseTabs from '@/components/ui/BaseTabs.vue'
import BaseTransferList from '@/components/ui/BaseTransferList.vue'
import { tradersService } from '@/api/services/traders.service'
import { merchantsService } from '@/api/services/merchants.service'
import { usersService } from '@/api/services/users.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import type { MerchantAdmin, Trader, TraderGroup, User } from '@/types'

const toast = useToast()
const { confirm } = useConfirm()

const loading = ref(false)
const saving = ref(false)
const search = ref('')
const groups = ref<TraderGroup[]>([])
const allTraders = ref<Trader[]>([])
const allMerchants = ref<MerchantAdmin[]>([])
const allTraderUsers = ref<Record<number, User>>({})

const showModal = ref(false)
const editing = ref<TraderGroup | null>(null)
const activeTab = ref('general')
const tabs = [
  { key: 'general', label: 'Основные' },
  { key: 'traders', label: 'Трейдеры' },
  { key: 'merchants', label: 'Мерчанты' },
]

const form = reactive({
  name: '',
  description: '',
  trader_ids: [] as number[],
  merchant_ids: [] as number[],
})

const filteredGroups = computed(() => {
  const t = search.value.trim().toLowerCase()
  if (!t) return groups.value
  return groups.value.filter(g =>
    g.name.toLowerCase().includes(t) ||
    (g.description ? g.description.toLowerCase().includes(t) : false),
  )
})

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
    const [groupsRes, tradersRes, merchantsRes, usersRes] = await Promise.all([
      tradersService.listGroups(),
      tradersService.list({ limit: 1000 }),
      merchantsService.listAll({ limit: 1000 }),
      usersService.list({ role: 'trader', limit: 1000 }),
    ])
    groups.value = groupsRes.data
    allTraders.value = tradersRes.data
    allMerchants.value = merchantsRes.data
    const map: Record<number, User> = {}
    for (const u of usersRes.data) map[u.id] = u
    allTraderUsers.value = map
  } catch {
    toast.error('Ошибка загрузки')
  } finally {
    loading.value = false
  }
}

function resetForm() {
  form.name = ''
  form.description = ''
  form.trader_ids = []
  form.merchant_ids = []
  activeTab.value = 'general'
}

function openCreate() {
  editing.value = null
  resetForm()
  showModal.value = true
}

function openEdit(g: TraderGroup) {
  editing.value = g
  form.name = g.name
  form.description = g.description || ''
  form.trader_ids = (g.traders || []).map(t => t.id)
  form.merchant_ids = (g.merchants || []).map(m => m.id)
  activeTab.value = 'general'
  showModal.value = true
}

async function save() {
  if (!form.name.trim()) {
    toast.error('Укажите название группы')
    return
  }
  saving.value = true
  try {
    if (editing.value) {
      await tradersService.updateGroup(editing.value.id, {
        name: form.name.trim(),
        description: form.description.trim() || undefined,
        trader_ids: form.trader_ids,
        merchant_ids: form.merchant_ids,
      })
    } else {
      const { data: created } = await tradersService.createGroup(
        form.name.trim(),
        form.description.trim() || undefined,
      )
      if (form.trader_ids.length || form.merchant_ids.length) {
        await tradersService.updateGroup(created.id, {
          trader_ids: form.trader_ids,
          merchant_ids: form.merchant_ids,
        })
      }
    }
    toast.success('Сохранено')
    showModal.value = false
    load()
  } catch {
    toast.error('Не удалось сохранить группу')
  } finally {
    saving.value = false
  }
}

async function askDelete(g: TraderGroup) {
  const ok = await confirm({
    title: 'Удалить группу?',
    message: `Группа «${g.name}» будет удалена. Все привязки трейдеров и мерчантов в этой группе исчезнут.`,
    confirmText: 'Удалить',
    cancelText: 'Отмена',
    variant: 'danger',
  })
  if (!ok) return
  try {
    await tradersService.deleteGroup(g.id)
    toast.success('Группа удалена')
    showModal.value = false
    load()
  } catch {
    toast.error('Не удалось удалить группу')
  }
}

onMounted(load)
</script>
