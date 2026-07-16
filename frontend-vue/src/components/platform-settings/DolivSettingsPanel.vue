<template>
  <div class="max-w-2xl space-y-6">
    <BaseCard>
      <div class="space-y-5">
        <div>
          <div class="mb-1.5 flex items-center gap-1.5">
            <span class="text-sm font-semibold text-text-secondary">Доливщики (ID пользователей)</span>
            <BaseHelp
              text="ID трейдеров-доливщиков через запятую. Только они видят пул доливов и могут брать/исполнять их. Остальные трейдеры видят пустой пул."
            />
          </div>
          <BaseInput
            v-model="form.executor_user_ids"
            placeholder="12, 34, 56"
            :disabled="loading || saving"
          />
        </div>

        <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <div class="mb-1.5 flex items-center gap-1.5">
              <span class="text-sm font-semibold text-text-secondary">Цена долива, %</span>
              <BaseHelp text="Сколько реквестер платит сверх суммы долива (процент от суммы в USDT). Замораживается вместе с суммой при создании." />
            </div>
            <BaseInput v-model.number="form.price_percent" type="number" min="0" max="100" step="0.1" :disabled="loading || saving" />
          </div>
          <div>
            <div class="mb-1.5 flex items-center gap-1.5">
              <span class="text-sm font-semibold text-text-secondary">Награда доливщику, %</span>
              <BaseHelp text="Вознаграждение доливщику за исполнение (процент от суммы в USDT). Выплачивается системой при исполнении." />
            </div>
            <BaseInput v-model.number="form.executor_reward_percent" type="number" min="0" max="100" step="0.1" :disabled="loading || saving" />
          </div>
          <div>
            <div class="mb-1.5 flex items-center gap-1.5">
              <span class="text-sm font-semibold text-text-secondary">Мин. сумма долива</span>
              <BaseHelp text="Минимальная сумма одного долива в фиате. 0 — без ограничения." />
            </div>
            <BaseInput v-model.number="form.min_amount" type="number" min="0" :disabled="loading || saving" />
          </div>
          <div>
            <div class="mb-1.5 flex items-center gap-1.5">
              <span class="text-sm font-semibold text-text-secondary">Макс. сумма долива</span>
              <BaseHelp text="Максимальная сумма одного долива в фиате. 0 — без ограничения." />
            </div>
            <BaseInput v-model.number="form.max_amount" type="number" min="0" :disabled="loading || saving" />
          </div>
        </div>

        <div class="flex justify-end gap-2">
          <BaseButton variant="dark" :disabled="!dirty || saving" @click="reset">Отменить</BaseButton>
          <BaseButton variant="gold" :loading="saving" :disabled="!dirty" @click="save">Сохранить</BaseButton>
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
import { platformSettingsService } from '@/api/services/platformSettings.service'
import { useToast } from '@/composables/useToast'
import type { DolivSettings } from '@/types'

const toast = useToast()
const loading = ref(false)
const saving = ref(false)

const blank = (): DolivSettings => ({
  min_amount: 0,
  max_amount: 0,
  price_percent: 0,
  executor_reward_percent: 0,
  executor_user_ids: '',
})

const initial = ref<DolivSettings>(blank())
const form = reactive<DolivSettings>(blank())

const dirty = computed(
  () =>
    form.min_amount !== initial.value.min_amount ||
    form.max_amount !== initial.value.max_amount ||
    form.price_percent !== initial.value.price_percent ||
    form.executor_reward_percent !== initial.value.executor_reward_percent ||
    form.executor_user_ids !== initial.value.executor_user_ids,
)

function apply(data: DolivSettings) {
  initial.value = data
  form.min_amount = data.min_amount
  form.max_amount = data.max_amount
  form.price_percent = data.price_percent
  form.executor_reward_percent = data.executor_reward_percent
  form.executor_user_ids = data.executor_user_ids
}

function reset() {
  apply(initial.value)
}

async function load() {
  loading.value = true
  try {
    const { data } = await platformSettingsService.getDolivSettings()
    apply(data)
  } catch {
    toast.error('Не удалось загрузить настройки долива')
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const { data } = await platformSettingsService.updateDolivSettings({
      min_amount: form.min_amount,
      max_amount: form.max_amount,
      price_percent: form.price_percent,
      executor_reward_percent: form.executor_reward_percent,
      executor_user_ids: form.executor_user_ids,
    })
    apply(data)
    toast.success('Сохранено')
  } catch (e: any) {
    toast.error(e?.response?.data?.detail || e?.response?.data?.message || 'Не удалось сохранить')
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>
