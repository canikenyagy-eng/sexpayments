<template>
  <div class="max-w-2xl space-y-6">
    <BaseCard>
      <div class="space-y-5">
        <div>
          <div class="mb-1.5 flex items-center gap-1.5">
            <span class="text-sm font-semibold text-text-secondary">
              Telegram chat_id группы уведомлений
            </span>
            <BaseHelp
              text="Добавьте support-bot в нужную группу и отправьте команду /id — он ответит chat_id. Это отдельная группа от модерации чеков. Пусто — уведомления не отправляются."
            />
          </div>
          <BaseInput
            v-model="form.notifications_chat_id"
            placeholder="-1001234567890"
            :disabled="loading || saving"
          />
        </div>

        <div class="border-t border-border pt-5">
          <div class="text-xs font-bold uppercase tracking-wider text-text-muted">
            О чём присылать
          </div>

          <div class="mt-3 flex items-center justify-between gap-4">
            <div class="text-sm font-bold text-text-main">Заявки на вывод</div>
            <BaseSwitch
              :model-value="form.notify_withdrawal_requests"
              :loading="saving"
              :disabled="loading"
              @update:model-value="(v: boolean) => (form.notify_withdrawal_requests = v)"
            />
          </div>
        </div>

        <div class="flex justify-end gap-2">
          <BaseButton variant="dark" :disabled="!dirty || saving" @click="reset">
            Отменить
          </BaseButton>
          <BaseButton variant="gold" :loading="saving" :disabled="!dirty" @click="save">
            Сохранить
          </BaseButton>
        </div>
      </div>
    </BaseCard>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseHelp from '@/components/ui/BaseHelp.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseSwitch from '@/components/ui/BaseSwitch.vue'
import { platformSettingsService } from '@/api/services/platformSettings.service'
import type { NotificationSettings } from '@/types'

const loading = ref(false)
const saving = ref(false)

// Server snapshot — drives the dirty flag and the cancel reset.
const initial = ref<NotificationSettings>({
  notifications_chat_id: '',
  notify_withdrawal_requests: false,
})

const form = reactive<NotificationSettings>({
  notifications_chat_id: '',
  notify_withdrawal_requests: false,
})

const dirty = computed(
  () =>
    form.notifications_chat_id !== initial.value.notifications_chat_id ||
    form.notify_withdrawal_requests !== initial.value.notify_withdrawal_requests,
)

function apply(data: NotificationSettings) {
  initial.value = data
  form.notifications_chat_id = data.notifications_chat_id
  form.notify_withdrawal_requests = data.notify_withdrawal_requests
}

async function load() {
  loading.value = true
  try {
    const { data } = await platformSettingsService.getNotificationSettings()
    apply(data)
  } finally {
    loading.value = false
  }
}

function reset() {
  form.notifications_chat_id = initial.value.notifications_chat_id
  form.notify_withdrawal_requests = initial.value.notify_withdrawal_requests
}

async function save() {
  saving.value = true
  try {
    const { data } = await platformSettingsService.updateNotificationSettings({
      notifications_chat_id: form.notifications_chat_id,
      notify_withdrawal_requests: form.notify_withdrawal_requests,
    })
    apply(data)
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
