<template>
  <div class="max-w-2xl space-y-6">
    <BaseCard>
      <div class="space-y-5">
        <div class="flex items-center justify-between gap-4">
          <div class="flex items-center gap-1.5">
            <div class="text-sm font-bold text-text-main">Глобальный тоггл</div>
            <BaseHelp
              text="Когда премодерация включена, чек от мерчанта сначала уходит в support-bot админ-чат с тремя кнопками: «Принять», «Запросить ПДФ» и «Запросить Видео». Только после нажатия «Принять» чек уходит трейдеру через trader-bot. Кнопки «Запросить ПДФ/Видео» отправляют уведомление мерчанту через merchant-notify-bot — для этого у мерчанта в карточке должен быть указан Telegram group ID. Действует на всю площадку; отдельным мерчантам можно прописать индивидуальный override в их карточке."
            />
          </div>
          <BaseSwitch
            :model-value="form.receipt_premoderation_enabled"
            :loading="saving"
            :disabled="loading"
            @update:model-value="onToggle"
          />
        </div>

        <div>
          <div class="mb-1.5 flex items-center gap-1.5">
            <span class="text-sm font-semibold text-text-secondary">
              Telegram chat_id support-бота
            </span>
            <BaseHelp
              text="ID чата, в который support-bot шлёт чеки. Добавьте бота в админ-группу и пришлите команду /id — он ответит chat_id. Должен совпадать с переменной SUPPORT_BOT_CHAT_ID в env."
            />
          </div>
          <BaseInput
            v-model="form.support_bot_chat_id"
            placeholder="-1001234567890"
            :disabled="loading || saving"
          />
        </div>

        <div>
          <div class="mb-1.5 flex items-center gap-1.5">
            <span class="text-sm font-semibold text-text-secondary">
              Напоминание о чеке (минут)
            </span>
            <BaseHelp
              text="Если на чек премодерации нет реакции дольше указанного времени, support-bot пришлёт reply-напоминание на исходную карточку чека и будет повторять каждые N минут, пока админ не нажмёт кнопку. 0 — напоминания выключены."
            />
          </div>
          <BaseInput
            v-model.number="form.premoderation_reminder_minutes"
            type="number"
            min="0"
            max="1440"
            placeholder="10"
            :disabled="loading || saving"
          />
        </div>

        <div class="flex justify-end gap-2">
          <BaseButton variant="dark" :disabled="!dirty || saving" @click="reset">
            Отменить
          </BaseButton>
          <BaseButton
            variant="gold"
            :loading="saving"
            :disabled="!dirty"
            @click="save"
          >
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
import type { PremoderationSettings } from '@/types'

const loading = ref(false)
const saving = ref(false)

// Server snapshot — used to compute the "dirty" flag and to reset on cancel.
const initial = ref<PremoderationSettings>({
  receipt_premoderation_enabled: false,
  support_bot_chat_id: '',
  premoderation_reminder_minutes: 10,
})

const form = reactive<PremoderationSettings>({
  receipt_premoderation_enabled: false,
  support_bot_chat_id: '',
  premoderation_reminder_minutes: 10,
})

const dirty = computed(
  () =>
    form.receipt_premoderation_enabled !== initial.value.receipt_premoderation_enabled ||
    form.support_bot_chat_id !== initial.value.support_bot_chat_id ||
    form.premoderation_reminder_minutes !== initial.value.premoderation_reminder_minutes,
)

async function load() {
  loading.value = true
  try {
    const { data } = await platformSettingsService.getPremoderationSettings()
    initial.value = data
    form.receipt_premoderation_enabled = data.receipt_premoderation_enabled
    form.support_bot_chat_id = data.support_bot_chat_id
    form.premoderation_reminder_minutes = data.premoderation_reminder_minutes
  } finally {
    loading.value = false
  }
}

function reset() {
  form.receipt_premoderation_enabled = initial.value.receipt_premoderation_enabled
  form.support_bot_chat_id = initial.value.support_bot_chat_id
  form.premoderation_reminder_minutes = initial.value.premoderation_reminder_minutes
}

function onToggle(value: boolean) {
  form.receipt_premoderation_enabled = value
}

async function save() {
  saving.value = true
  try {
    const { data } = await platformSettingsService.updatePremoderationSettings({
      receipt_premoderation_enabled: form.receipt_premoderation_enabled,
      support_bot_chat_id: form.support_bot_chat_id,
      premoderation_reminder_minutes: form.premoderation_reminder_minutes,
    })
    initial.value = data
    form.receipt_premoderation_enabled = data.receipt_premoderation_enabled
    form.support_bot_chat_id = data.support_bot_chat_id
    form.premoderation_reminder_minutes = data.premoderation_reminder_minutes
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
