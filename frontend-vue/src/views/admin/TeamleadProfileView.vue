<template>
  <div>
    <PageHeader
      :title="teamlead ? `Тимлид: ${teamlead.username}` : 'Профиль тимлида'"

    >
      <template #actions>
        <BaseButton variant="dark" @click="router.push('/admin/teamleads')">Назад к списку</BaseButton>
        <BaseButton variant="gold" @click="openCreate">+ Создать связь</BaseButton>
      </template>
    </PageHeader>

    <BaseCard>
      <div v-if="loading" class="py-8 text-center"><LoadingSpinner /></div>
      <div v-else-if="!links.length" class="py-8 text-center text-text-muted">Нет связей. Создайте первую.</div>
      <div v-else class="space-y-3">
        <div
          v-for="link in links"
          :key="`${link.teamlead_id}-${link.linked_entity_id}`"
          class="flex items-center justify-between rounded-xl bg-bg-card p-4"
        >
          <div class="space-y-1 text-sm">
            <div>
              <span class="text-text-muted">Привязан к:</span>
              <BaseBadge :color="link.linked_entity_type === 'merchant' ? 'gold' : 'info'" class="ml-1">
                {{ link.linked_entity_type }} #{{ link.linked_entity_id }}
              </BaseBadge>
            </div>
            <div>
              <span class="text-text-muted">Fee (ордер):</span> <strong class="text-accent">{{ link.fee_percent }}%</strong>
              <span class="ml-3 text-text-muted">Fee (выплата):</span> <strong class="text-accent">{{ link.payout_fee_percent }}%</strong>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <StatusBadge :status="link.is_active ? 'enabled' : 'disabled'" />
            <BaseButton action="edit" variant="ghost" size="sm" @click="openEditLink(link)" />
            <BaseButton action="delete" variant="ghost" size="sm" @click="removeLink(link)" />
          </div>
        </div>
      </div>
    </BaseCard>

    <!-- Create modal -->
    <BaseModal v-model="showCreate" title="Новая связь">
      <div class="space-y-4">
        <BaseSelect
          v-model="createForm.linked_entity_type"
          label="Тип привязки"
          :options="[{ value: 'merchant', label: 'Мерчант' }, { value: 'trader', label: 'Трейдер' }]"
        />
        <BaseInput v-model="createForm.linked_entity_id" label="Entity ID" type="number" required />
        <BaseInput v-model="createForm.fee_percent" label="Fee % (ордеры)" type="number" />
        <BaseInput v-model="createForm.payout_fee_percent" label="Fee % (выплаты)" type="number" />
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showCreate = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="saving" @click="createLink">Создать</BaseButton>
      </template>
    </BaseModal>

    <!-- Edit modal -->
    <BaseModal v-model="showEditModal" title="Редактирование связи">
      <div class="space-y-4">
        <BaseInput v-model="editForm.fee_percent" label="Fee % (ордеры)" type="number" />
        <BaseInput v-model="editForm.payout_fee_percent" label="Fee % (выплаты)" type="number" />
        <BaseSelect
          v-model="editForm.is_active"
          label="Активен"
          :options="[{ value: 'true', label: 'Да' }, { value: 'false', label: 'Нет' }]"
        />
      </div>
      <template #footer>
        <BaseButton variant="dark" @click="showEditModal = false">Отмена</BaseButton>
        <BaseButton variant="gold" :loading="saving" @click="saveEditLink">Сохранить</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import PageHeader from '@/components/layout/PageHeader.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseBadge from '@/components/ui/BaseBadge.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import { teamleadsService } from '@/api/services/teamleads.service'
import { usersService } from '@/api/services/users.service'
import { useToast } from '@/composables/useToast'
import { useConfirm } from '@/composables/useConfirm'
import type { TeamleadLink, User } from '@/types'

const toast = useToast()
const { confirm } = useConfirm()
const router = useRouter()
const route = useRoute()

const teamleadId = Number(route.params.id)
const teamlead = ref<User | null>(null)

const loading = ref(false)
const saving = ref(false)
const links = ref<TeamleadLink[]>([])

const showCreate = ref(false)
const createForm = reactive({ linked_entity_type: 'merchant', linked_entity_id: '', fee_percent: '0', payout_fee_percent: '0' })

const showEditModal = ref(false)
const editingLink = ref<TeamleadLink | null>(null)
const editForm = reactive({ fee_percent: '0', payout_fee_percent: '0', is_active: 'true' })

async function load() {
  loading.value = true
  try {
    const [userRes, linksRes] = await Promise.all([
      usersService.getById(teamleadId),
      teamleadsService.listAllLinks({ limit: 200, teamlead_id: teamleadId })
    ])
    teamlead.value = userRes.data
    links.value = linksRes.data
  } catch { toast.error('Ошибка загрузки данных') }
  finally { loading.value = false }
}

function openCreate() { showCreate.value = true }

async function createLink() {
  saving.value = true
  try {
    const newLink = await teamleadsService.createLink({
      teamlead_id: teamleadId,
      linked_entity_type: createForm.linked_entity_type as 'merchant' | 'trader',
      linked_entity_id: Number(createForm.linked_entity_id),
      fee_percent: Number(createForm.fee_percent),
      payout_fee_percent: Number(createForm.payout_fee_percent),
    })
    links.value.push(newLink.data)
    toast.success('Связь создана')
    showCreate.value = false
  } catch (e: any) {
    toast.error(e.response?.data?.detail || 'Ошибка создания')
  } finally { saving.value = false }
}

function openEditLink(link: TeamleadLink) {
  editingLink.value = link
  editForm.fee_percent = String(link.fee_percent)
  editForm.payout_fee_percent = String(link.payout_fee_percent ?? 0)
  editForm.is_active = String(link.is_active)
  showEditModal.value = true
}

async function saveEditLink() {
  if (!editingLink.value?.id) return
  saving.value = true
  try {
    await teamleadsService.updateLink(editingLink.value.id, {
      fee_percent: Number(editForm.fee_percent),
      is_active: editForm.is_active === 'true',
    })
    toast.success('Связь обновлена')
    showEditModal.value = false
    load()
  } catch { toast.error('Ошибка обновления') }
  finally { saving.value = false }
}

async function removeLink(link: TeamleadLink) {
  const ok = await confirm({
    title: 'Удалить связь',
    message: `Связь с ${link.linked_entity_type} #${link.linked_entity_id} будет удалена. История выплат сохранится.`,
    confirmText: 'Удалить',
    cancelText: 'Отмена',
    variant: 'danger',
  })
  if (!ok || !link.id) return

  try {
    await teamleadsService.deleteLink(link.id)
    links.value = links.value.filter(l => l.id !== link.id)
    toast.success('Связь удалена')
  } catch {
    toast.error('Не удалось удалить связь')
  }
}

onMounted(load)
</script>
