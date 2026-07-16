<template>
  <div>
    <PageHeader title="Настройки" />

    <div v-if="loading" class="py-16"><LoadingSpinner /></div>

    <template v-else-if="profile">
      <div class="grid grid-cols-1 gap-6">
        <BaseCard title="Telegram Bot — доступ">
          <div class="space-y-4">
            <p class="text-sm text-text-muted">
              Telegram User ID пользователей, которым разрешено использовать бота.
            </p>

            <div class="flex flex-col gap-2">
              <div
                v-for="(tg, idx) in tgUsers"
                :key="tg.id"
                class="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-bg-card px-3 py-2"
              >
                <span class="font-mono text-xs text-accent">{{ tg.id }}</span>
                <BaseInput
                  v-model="tgUsers[idx].label"
                  placeholder="Название (например: сотрудник)"
                  class="flex-1 min-w-[200px]"
                />
                <button
                  type="button"
                  class="text-text-muted hover:text-status-danger"
                  @click="removeTgId(tg.id)"
                >×</button>
              </div>
              <p v-if="!tgUsers.length" class="text-sm text-text-muted">
                Нет добавленных пользователей.
              </p>
            </div>

            <div class="flex flex-wrap items-end gap-2">
              <BaseInput
                v-model="newTgIdInput"
                label="Telegram User ID"
                placeholder="12345678"
                type="number"
                class="flex-1 min-w-[160px]"
              />
              <BaseInput
                v-model="newTgLabelInput"
                label="Название (необязательно)"
                placeholder="Сотрудник"
                class="flex-1 min-w-[200px]"
              />
              <BaseButton variant="dark" size="sm" @click="addTgId">Добавить</BaseButton>
            </div>

            <BaseButton variant="gold" size="sm" :loading="savingTg" @click="saveTgUsers">
              Сохранить список
            </BaseButton>
          </div>
        </BaseCard>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import { merchantsService } from '@/api/services/merchants.service'
import { useToast } from '@/composables/useToast'
import type { MerchantFullProfile, TelegramUser } from '@/types'

const toast = useToast()

interface TgEdit { id: number; label: string }

const loading = ref(true)
const savingTg = ref(false)

const profile = ref<MerchantFullProfile | null>(null)
const tgUsers = ref<TgEdit[]>([])
const newTgIdInput = ref('')
const newTgLabelInput = ref('')

function hydrateTgUsers(list?: TelegramUser[] | number[] | null) {
  tgUsers.value = (list ?? []).map(t =>
    typeof t === 'number'
      ? { id: t, label: '' }
      : { id: t.id, label: t.label ?? '' },
  )
}

async function loadProfile() {
  loading.value = true
  try {
    const { data } = await merchantsService.getMyProfile()
    profile.value = data
    hydrateTgUsers(data.telegram_user_ids)
  } catch {
    toast.error('Ошибка загрузки профиля')
  } finally {
    loading.value = false
  }
}

function addTgId() {
  const id = parseInt(newTgIdInput.value)
  if (!id || tgUsers.value.some(t => t.id === id)) {
    newTgIdInput.value = ''
    newTgLabelInput.value = ''
    return
  }
  tgUsers.value.push({ id, label: newTgLabelInput.value.trim() })
  newTgIdInput.value = ''
  newTgLabelInput.value = ''
}

function removeTgId(id: number) {
  tgUsers.value = tgUsers.value.filter(t => t.id !== id)
}

async function saveTgUsers() {
  savingTg.value = true
  try {
    const payload = tgUsers.value.map(t => ({
      id: t.id,
      label: t.label.trim() || null,
    }))
    const { data } = await merchantsService.updateSettings({ telegram_user_ids: payload })
    profile.value = data
    hydrateTgUsers(data.telegram_user_ids)
    toast.success('Список пользователей сохранён')
  } catch {
    toast.error('Ошибка сохранения')
  } finally {
    savingTg.value = false
  }
}

onMounted(loadProfile)
</script>
