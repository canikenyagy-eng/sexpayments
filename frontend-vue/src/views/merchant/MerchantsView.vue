<template>
  <div>
    <PageHeader title="Мои терминалы">
      <template #actions>
        <BaseButton variant="gold" @click="showCreate = true">+ Создать терминал</BaseButton>
      </template>
    </PageHeader>

    <div v-if="loading" class="py-12 text-center"><LoadingSpinner /></div>

    <div v-else-if="!merchants.length" class="py-12 text-center text-text-muted">
      <p class="mb-4 text-lg">У вас пока нет терминалов</p>
      <BaseButton variant="gold" @click="showCreate = true">+ Создать первый терминал</BaseButton>
    </div>

    <div v-else class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <router-link
        v-for="m in merchants"
        :key="m.id"
        :to="`/merchant/terminals/${m.id}`"
        class="block rounded-2xl border border-border bg-bg-card p-5 no-underline transition hover:border-accent/40 hover:shadow-lg"
      >
        <div class="mb-3 flex items-center justify-between">
          <h3 class="text-lg font-bold text-text-main">{{ m.name || 'Без названия' }}</h3>
          <StatusBadge :status="m.status" context="merchant" />
        </div>
        <div class="space-y-2 text-sm">
          <div class="flex justify-between">
            <span class="text-text-muted">Валюта</span>
            <span class="text-text-main">{{ m.currency }}</span>
          </div>
          <div class="flex justify-between">
            <span class="text-text-muted">API Key</span>
            <span class="font-mono text-xs text-text-secondary">{{ m.api_key_masked }}</span>
          </div>
          <div v-if="m.webhook_url" class="flex justify-between">
            <span class="text-text-muted">Webhook</span>
            <span class="max-w-[180px] truncate text-xs text-text-secondary">{{ m.webhook_url }}</span>
          </div>
        </div>
      </router-link>
    </div>

    <!-- Create terminal modal -->
    <BaseModal v-model="showCreate" title="Новый терминал">
      <div class="space-y-4">
        <BaseInput v-model="createName" label="Название (опционально)" placeholder="Мой магазин" />
      </div>
      <template #footer>
        <div class="flex justify-end gap-2">
          <BaseButton variant="ghost" @click="showCreate = false">Отмена</BaseButton>
          <BaseButton variant="gold" :loading="creating" @click="createMerchant">Создать</BaseButton>
        </div>
      </template>
    </BaseModal>

    <!-- Show new keys modal -->
    <BaseModal v-model="showKeys" title="Терминал создан">
      <div class="space-y-4">
        <div class="rounded-xl bg-status-warning/10 p-4 text-sm text-status-warning">
          Сохраните API Secret — он показывается только один раз!
        </div>
        <div class="space-y-2">
          <div>
            <p class="mb-1 text-xs text-text-muted">API Key</p>
            <div class="flex items-center gap-2 rounded-lg bg-bg-surface px-3 py-2">
              <code class="flex-1 break-all text-sm text-text-main">{{ newKeys?.api_key }}</code>
              <BaseButton variant="ghost" size="sm" @click="copy(newKeys?.api_key ?? '')">Копировать</BaseButton>
            </div>
          </div>
          <div>
            <p class="mb-1 text-xs text-text-muted">API Secret</p>
            <div class="flex items-center gap-2 rounded-lg bg-bg-surface px-3 py-2">
              <code class="flex-1 break-all text-sm text-accent">{{ newKeys?.api_secret }}</code>
              <BaseButton variant="ghost" size="sm" @click="copy(newKeys?.api_secret ?? '')">Копировать</BaseButton>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <BaseButton variant="gold" @click="showKeys = false">Понятно</BaseButton>
      </template>
    </BaseModal>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import { merchantsService } from '@/api/services/merchants.service'
import { useToast } from '@/composables/useToast'
import type { MerchantListItem, MerchantCreateResponse } from '@/types'

const toast = useToast()

const loading = ref(false)
const creating = ref(false)
const merchants = ref<MerchantListItem[]>([])
const showCreate = ref(false)
const showKeys = ref(false)
const createName = ref('')
const newKeys = ref<MerchantCreateResponse | null>(null)

async function load() {
  loading.value = true
  try {
    const { data } = await merchantsService.listMyMerchants()
    merchants.value = data
  } catch {
    toast.error('Ошибка загрузки терминалов')
  } finally {
    loading.value = false
  }
}

async function createMerchant() {
  creating.value = true
  try {
    const { data } = await merchantsService.createMerchant({
      name: createName.value.trim() || undefined,
    })
    newKeys.value = data
    showCreate.value = false
    showKeys.value = true
    createName.value = ''
    toast.success('Терминал создан')
    load()
  } catch (e: any) {
    toast.error(e?.response?.data?.error_message || 'Ошибка создания')
  } finally {
    creating.value = false
  }
}

function copy(text: string) {
  navigator.clipboard.writeText(text)
  toast.success('Скопировано')
}

onMounted(load)
</script>
