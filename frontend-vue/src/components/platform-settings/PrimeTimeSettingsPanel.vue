<template>
  <div class="max-w-2xl space-y-6">
    <BaseCard>
      <div class="space-y-5">
        <div class="text-sm text-text-secondary">
          Временное глобальное увеличение ставки <strong>всем трейдерам</strong> на
          заданное число пунктов. Бонус фиксируется в момент создания сделки —
          сделка сохраняет его, даже если завершится после окончания окна.
        </div>

        <!-- Active window -->
        <div
          v-if="active"
          class="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-accent/30 bg-accent/10 px-4 py-3"
        >
          <div class="flex items-center gap-2 text-accent">
            <Zap class="h-5 w-5 shrink-0" />
            <span class="font-bold">Primetime +{{ points }}%</span>
            <span v-if="remainingLabel" class="font-mono text-sm opacity-80">
              осталось {{ remainingLabel }}
            </span>
          </div>
          <BaseButton variant="danger" size="sm" :loading="busy" @click="stop">
            Остановить
          </BaseButton>
        </div>

        <!-- Activate / restart -->
        <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <label class="mb-1.5 block text-sm font-semibold text-text-secondary">
              Процент (пункты)
            </label>
            <BaseInput v-model="form.points" type="number" min="0.01" step="0.01" placeholder="напр. 0.5" />
          </div>
          <div>
            <label class="mb-1.5 block text-sm font-semibold text-text-secondary">
              Длительность (мин)
            </label>
            <BaseInput v-model="form.minutes" type="number" min="1" step="1" placeholder="напр. 30" />
          </div>
        </div>

        <div class="flex justify-end">
          <BaseButton variant="gold" :loading="busy" :disabled="!valid" @click="activate">
            {{ active ? 'Перезапустить' : 'Запустить' }}
          </BaseButton>
        </div>
      </div>
    </BaseCard>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { Zap } from 'lucide-vue-next'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import { platformSettingsService } from '@/api/services/platformSettings.service'
import { usePrimeTime } from '@/composables/usePrimeTime'
import { useToast } from '@/composables/useToast'

const { active, points, remainingLabel, refresh, start } = usePrimeTime()
const toast = useToast()
const busy = ref(false)

const form = reactive({ points: '', minutes: '' })

start() // ensure the live state polls while this panel is open

const valid = computed(() => Number(form.points) > 0 && Number(form.minutes) >= 1)

async function activate() {
  if (!valid.value) return
  busy.value = true
  try {
    await platformSettingsService.activatePrimeTime({
      points: Number(form.points),
      minutes: Number(form.minutes),
    })
    await refresh()
    toast.success('Prime-Time запущен')
  } catch {
    toast.error('Не удалось запустить Prime-Time')
  } finally {
    busy.value = false
  }
}

async function stop() {
  busy.value = true
  try {
    await platformSettingsService.stopPrimeTime()
    await refresh()
    toast.success('Prime-Time остановлен')
  } catch {
    toast.error('Не удалось остановить Prime-Time')
  } finally {
    busy.value = false
  }
}
</script>
