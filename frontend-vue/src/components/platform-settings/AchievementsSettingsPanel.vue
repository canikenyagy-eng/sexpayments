<template>
  <div class="max-w-2xl space-y-6">
    <BaseCard>
      <div class="space-y-5">
        <div class="text-sm text-text-secondary">
          Один бонус к ставке: трейдер разблокирует его, удержав стрик из N дней
          подряд с оборотом ≥ порога, а РАЗМЕР бонуса берётся по среднему обороту
          за эти дни. Стрик не набран — бонус 0. Пересчёт фоновый (каждые 5 мин).
        </div>

        <!-- Master toggle -->
        <div class="flex items-center justify-between gap-4">
          <div class="flex items-center gap-1.5">
            <span class="text-sm font-bold text-text-main">Система достижений включена</span>
            <BaseHelp text="Мастер-тоггл. Выключено — фоновая джоба не начисляет бонусы и обнуляет уже выданные (бонус = 0)." />
          </div>
          <BaseSwitch
            :model-value="form.enabled"
            :loading="saving"
            :disabled="loading"
            @update:model-value="(v: boolean) => (form.enabled = v)"
          />
        </div>

        <!-- Cap -->
        <div>
          <div class="mb-1.5 flex items-center gap-1.5">
            <span class="text-sm font-semibold text-text-secondary">Кап суммарного бонуса, %</span>
            <BaseHelp text="Максимум суммарного бонуса к ставке (в п.п.), если правил несколько. 0 — без капа." />
          </div>
          <BaseInput v-model.number="form.bonus_max_percent" type="number" min="0" step="0.1" :disabled="loading || saving" />
        </div>
      </div>
    </BaseCard>

    <!-- Rules -->
    <div class="space-y-4">
      <div class="flex items-center justify-between">
        <div class="flex items-center gap-1.5">
          <span class="text-sm font-bold text-text-main">Правила бонуса</span>
          <BaseHelp text="Каждое правило — свой стрик-гейт + лестница уровней по среднему обороту. Вклады правил суммируются (с учётом капа). Объём — в USDT." />
        </div>
        <BaseButton variant="ghost" size="sm" :disabled="loading || saving" @click="addRule">
          + Правило
        </BaseButton>
      </div>

      <p v-if="!form.rules.length" class="text-sm text-text-muted">
        Правил нет — бонус ни у кого не начисляется. Добавьте хотя бы одно правило.
      </p>

      <BaseCard v-for="(rule, ri) in form.rules" :key="ri">
        <div class="space-y-4">
          <div class="flex items-center justify-between gap-3">
            <span class="text-sm font-bold text-text-main">Правило {{ ri + 1 }}</span>
            <BaseButton variant="danger" size="sm" :disabled="loading || saving" @click="removeRule(ri)">
              Удалить
            </BaseButton>
          </div>

          <div class="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <div class="mb-1.5 flex items-center gap-1.5">
                <span class="text-sm font-semibold text-text-secondary">Дней подряд (стрик)</span>
                <BaseHelp text="Сколько дней подряд с оборотом ≥ порога нужно удержать, чтобы разблокировать бонус. Это же — окно усреднения оборота." />
              </div>
              <BaseInput v-model.number="rule.streak_days" type="number" min="1" step="1" :disabled="loading || saving" />
            </div>
            <div>
              <div class="mb-1.5 flex items-center gap-1.5">
                <span class="text-sm font-semibold text-text-secondary">Мин. оборот за день, USDT</span>
                <BaseHelp text="День засчитывается в стрик, только если его оборот ≥ этого порога (и > 0). Один день ниже — стрик сгорает." />
              </div>
              <BaseInput v-model.number="rule.min_daily_volume" type="number" min="0" step="1" :disabled="loading || saving" />
            </div>
          </div>

          <div>
            <div class="mb-1.5 flex items-center gap-1.5">
              <span class="text-sm font-semibold text-text-secondary">Уровни: средний оборот от → бонус, %</span>
              <BaseHelp text="Размер бонуса по среднему дневному обороту за X дней. Берётся самый высокий достигнутый уровень. Напр. ≥1000 → +0.5, ≥5000 → +1.5." />
            </div>
            <div class="space-y-2">
              <div v-for="(tier, ti) in rule.tiers" :key="ti" class="grid grid-cols-[1fr_1fr_auto] items-center gap-2">
                <BaseInput v-model.number="tier.min_avg" type="number" min="0" step="1" placeholder="средний оборот от" :disabled="loading || saving" />
                <BaseInput v-model.number="tier.percent" type="number" min="0" step="0.01" placeholder="бонус, %" :disabled="loading || saving" />
                <button type="button" class="text-xs text-status-danger hover:underline" @click="removeTier(ri, ti)">×</button>
              </div>
            </div>
            <BaseButton variant="ghost" size="sm" class="mt-2" :disabled="loading || saving" @click="addTier(ri)">
              + Уровень
            </BaseButton>
          </div>
        </div>
      </BaseCard>
    </div>

    <!-- Actions -->
    <div class="flex flex-wrap items-center justify-between gap-2">
      <BaseButton
        variant="dark"
        :loading="recomputing"
        :disabled="dirty || saving || loading"
        :title="dirty ? 'Сначала сохраните изменения' : 'Пересчитать бонусы всех трейдеров сейчас, не дожидаясь 5-минутного цикла'"
        @click="recompute"
      >
        Пересчитать сейчас
      </BaseButton>
      <div class="flex gap-2">
        <BaseButton variant="dark" :disabled="!dirty || saving" @click="reset">Отменить</BaseButton>
        <BaseButton variant="gold" :loading="saving" :disabled="!dirty" @click="save">Сохранить</BaseButton>
      </div>
    </div>
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
import { tradersService } from '@/api/services/traders.service'
import { useToast } from '@/composables/useToast'
import type { AchievementRule, AchievementSettings, AchievementSettingsUpdate } from '@/types'

const toast = useToast()
const loading = ref(false)
const saving = ref(false)
const recomputing = ref(false)

const num = (v: unknown): number => {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

function blankRule(): AchievementRule {
  return { type: 'streak_volume_tier', streak_days: 3, min_daily_volume: 0, tiers: [{ min_avg: 0, percent: 0 }] }
}

// Server rules come back as loose dicts (numbers possibly as strings) — normalize
// into the typed, number-based form shape.
function hydrateRules(raw: AchievementRule[] | undefined): AchievementRule[] {
  return (raw ?? []).map((r) => ({
    type: 'streak_volume_tier',
    streak_days: Math.max(1, Math.round(num(r.streak_days))),
    min_daily_volume: num(r.min_daily_volume),
    tiers: (r.tiers ?? []).map((t) => ({ min_avg: num(t.min_avg), percent: num(t.percent) })),
  }))
}

function serializeRules(rules: AchievementRule[]): AchievementRule[] {
  return rules.map((r) => ({
    type: 'streak_volume_tier',
    streak_days: Math.max(1, Math.round(num(r.streak_days))),
    min_daily_volume: num(r.min_daily_volume),
    tiers: r.tiers.map((t) => ({ min_avg: num(t.min_avg), percent: num(t.percent) })),
  }))
}

function payload(): AchievementSettingsUpdate {
  return {
    enabled: form.enabled,
    bonus_max_percent: num(form.bonus_max_percent),
    rules: serializeRules(form.rules),
  }
}

const blank = (): AchievementSettings => ({ enabled: false, bonus_max_percent: 0, rules: [] })

const initial = ref<AchievementSettings>(blank())
const initialSnapshot = ref('')
const form = reactive<AchievementSettings>(blank())

const dirty = computed(() => JSON.stringify(payload()) !== initialSnapshot.value)

function apply(data: AchievementSettings) {
  initial.value = data
  form.enabled = data.enabled
  form.bonus_max_percent = num(data.bonus_max_percent)
  form.rules = hydrateRules(data.rules)
  initialSnapshot.value = JSON.stringify(payload())
}

function reset() {
  apply(initial.value)
}

function addRule() {
  form.rules.push(blankRule())
}

function removeRule(ri: number) {
  form.rules.splice(ri, 1)
}

function addTier(ri: number) {
  form.rules[ri].tiers.push({ min_avg: 0, percent: 0 })
}

function removeTier(ri: number, ti: number) {
  form.rules[ri].tiers.splice(ti, 1)
}

async function load() {
  loading.value = true
  try {
    const { data } = await platformSettingsService.getAchievementSettings()
    apply(data)
  } catch {
    toast.error('Не удалось загрузить настройки достижений')
  } finally {
    loading.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const { data } = await platformSettingsService.updateAchievementSettings(payload())
    apply(data)
    toast.success('Сохранено')
  } catch (e: any) {
    const msg = e?.response?.data?.detail || e?.response?.data?.message || 'Не удалось сохранить'
    toast.error(typeof msg === 'string' ? msg : 'Не удалось сохранить')
  } finally {
    saving.value = false
  }
}

async function recompute() {
  recomputing.value = true
  try {
    const { data } = await tradersService.recomputeAchievements()
    toast.success(`Пересчитано, изменено трейдеров: ${data.changed}`)
  } catch {
    toast.error('Не удалось пересчитать')
  } finally {
    recomputing.value = false
  }
}

onMounted(load)
</script>
