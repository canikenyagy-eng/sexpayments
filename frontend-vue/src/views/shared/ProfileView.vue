<template>
  <div>
    <PageHeader title="Профиль" />

    <div class="grid grid-cols-1 gap-6 lg:grid-cols-2">
      <!-- Account info -->
      <BaseCard title="Информация">
        <div v-if="user" class="space-y-3">
          <div class="flex items-center gap-4">
            <div class="flex h-14 w-14 items-center justify-center rounded-2xl bg-accent/15 text-xl font-black text-accent">
              {{ user.username.charAt(0).toUpperCase() }}
            </div>
            <div>
              <p class="text-lg font-bold text-text-main">{{ user.username }}</p>
              <p class="text-sm text-text-muted">{{ roleLabel }}</p>
            </div>
          </div>
          <div class="mt-4 space-y-2 text-sm">
            <div class="flex justify-between rounded-lg bg-bg-card px-3 py-2">
              <span class="text-text-muted">2FA</span>
              <span :class="user.totp_enabled ? 'text-status-success' : 'text-status-danger'">
                {{ user.totp_enabled ? 'Включена' : 'Выключена' }}
              </span>
            </div>
            <div class="flex justify-between rounded-lg bg-bg-card px-3 py-2">
              <span class="text-text-muted">Дата создания</span>
              <span class="text-text-main">{{ formatDateOnly(user.created_at) }}</span>
            </div>
          </div>
        </div>
      </BaseCard>

      <!-- Timezone -->
      <BaseCard title="Часовой пояс">
        <div class="space-y-3">
          <BaseSelect
            v-model="tzValue"
            label="Часовой пояс"
            :options="tzOptions"
          />
          <div class="rounded-lg bg-bg-card px-3 py-2 text-sm">
            <span class="text-text-muted">Текущее время: </span>
            <span class="text-text-main">{{ currentTzPreview }}</span>
          </div>
          <BaseButton
            variant="gold"
            :loading="tzLoading"
            :disabled="!tzChanged"
            @click="saveTimezone"
          >Сохранить</BaseButton>
        </div>
      </BaseCard>

      <!-- Change password -->
      <BaseCard title="Сменить пароль">
        <form class="space-y-4" @submit.prevent="changePassword">
          <BaseInput v-model="pwForm.new_password" label="Новый пароль" type="password" required />
          <BaseInput v-model="pwForm.new_password_confirm" label="Повторите пароль" type="password" required />
          <BaseInput v-if="user?.totp_enabled" v-model="pwForm.google_code" label="Код 2FA" placeholder="123456" required />
          <BaseButton type="submit" variant="gold" :loading="pwLoading">Сменить пароль</BaseButton>
        </form>
      </BaseCard>

      <!-- Receipt check (trader only): default provider + auto-check -->
      <BaseCard v-if="user?.role === 'trader'" title="Проверка чеков">
        <div v-if="receiptLoading" class="py-2 text-sm text-text-muted">Загрузка…</div>
        <div v-else class="space-y-4">
          <div v-if="providers.length === 0" class="rounded-lg bg-status-warning/10 px-3 py-2 text-sm text-status-warning">
            Сервис проверки временно недоступен
          </div>
          <div v-else class="space-y-2">
            <p class="font-semibold text-text-main">Провайдер по умолчанию</p>
            <p class="text-xs text-text-muted">Предвыбирается при ручной проверке чека и используется для авто-проверки.</p>
            <ReceiptProviderRadioGroup
              :model-value="defaultProviderId"
              :providers="providers"
              :disabled="defaultProviderSaving"
              @update:model-value="saveDefaultProvider"
            />
          </div>

          <div class="flex items-center justify-between gap-3 border-t border-border pt-3">
            <p class="font-semibold text-text-main">Автоматически проверять каждый загруженный чек</p>
            <BaseSwitch
              :model-value="!!traderProfile?.receipt_auto_check"
              :loading="autoCheckSaving"
              @update:model-value="toggleReceiptAutoCheck"
            />
          </div>
        </div>
      </BaseCard>

      <!-- 2FA setup -->
      <BaseCard v-if="!user?.totp_enabled" title="Настройка 2FA">
        <div v-if="!setup2FA" class="space-y-3">
          <p class="text-sm text-text-muted">Двухфакторная аутентификация не включена. Рекомендуем включить для безопасности.</p>
          <BaseButton variant="gold" :loading="setupLoading" @click="startSetup2FA">Начать настройку</BaseButton>
        </div>
        <div v-else class="space-y-4">
          <p class="text-sm text-text-secondary">Отсканируйте QR-код в приложении аутентификации или введите секрет вручную:</p>
          <div class="rounded-xl bg-bg-card p-3 font-mono text-xs text-accent break-all">{{ setup2FA.secret }}</div>
          <form class="flex gap-2" @submit.prevent="enable2FA">
            <BaseInput v-model="totpCode" placeholder="Код из приложения" required />
            <BaseButton type="submit" variant="gold" :loading="enableLoading">Подтвердить</BaseButton>
          </form>
        </div>
      </BaseCard>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import dayjs from 'dayjs'
import PageHeader from '@/components/layout/PageHeader.vue'
import BaseCard from '@/components/ui/BaseCard.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import BaseSelect from '@/components/ui/BaseSelect.vue'
import BaseSwitch from '@/components/ui/BaseSwitch.vue'
import ReceiptProviderRadioGroup from '@/components/receipt-check/ReceiptProviderRadioGroup.vue'
import { useAuthStore } from '@/stores/auth'
import { usersService } from '@/api/services/users.service'
import { tradersService } from '@/api/services/traders.service'
import { receiptChecksService } from '@/api/services/receiptChecks.service'
import { useToast } from '@/composables/useToast'
import { formatDateOnly, getBrowserTz, listCommonTimezones } from '@/utils/datetime'
import type { Setup2FAResponse, Trader, TraderReceiptProvider } from '@/types'

const toast = useToast()
const authStore = useAuthStore()
const user = computed(() => authStore.user)

const roleLabelMap: Record<string, string> = {
  admin: 'Администратор', merchant: 'Мерчант', trader: 'Трейдер', teamlead: 'Тимлид',
}
const roleLabel = computed(() => roleLabelMap[user.value?.role ?? ''] ?? '')

const pwForm = reactive({ new_password: '', new_password_confirm: '', google_code: '' })
const pwLoading = ref(false)

const setup2FA = ref<Setup2FAResponse | null>(null)
const setupLoading = ref(false)
const totpCode = ref('')
const enableLoading = ref(false)

// ─── Timezone ──────────────────────────────────────────
const tzOptions = computed(() => [
  { value: '', label: `Авто — браузер (${getBrowserTz()})` },
  ...listCommonTimezones().map((tz) => ({ value: tz.value, label: tz.label })),
])
const tzValue = ref<string>(user.value?.timezone ?? '')
const tzLoading = ref(false)
const tzChanged = computed(() => (tzValue.value || null) !== (user.value?.timezone ?? null))

const previewTick = ref(0)
let previewInterval: ReturnType<typeof setInterval> | null = null

// ─── Receipt check (trader-only block) ────────────────────
const traderProfile = ref<Trader | null>(null)
const providers = ref<TraderReceiptProvider[]>([])
const defaultProviderId = ref<number | null>(null)
const receiptLoading = ref(false)
const autoCheckSaving = ref(false)
const defaultProviderSaving = ref(false)

async function loadReceiptCheckBlock() {
  if (user.value?.role !== 'trader') return
  receiptLoading.value = true
  try {
    const [profileR, providersR] = await Promise.all([
      tradersService.getMe(),
      receiptChecksService.listProviders(),
    ])
    traderProfile.value = profileR.data
    providers.value = providersR.data
    const saved = profileR.data.default_receipt_check_provider_id ?? null
    // Only reflect the saved default if it's still an active option.
    defaultProviderId.value =
      saved != null && providers.value.some((p) => p.id === saved) ? saved : null
  } catch (e: any) {
    toast.error('Не удалось загрузить настройки проверки чеков')
  } finally {
    receiptLoading.value = false
  }
}

async function saveDefaultProvider(providerId: number | null) {
  defaultProviderSaving.value = true
  try {
    const { data } = await tradersService.setDefaultReceiptProvider(providerId)
    traderProfile.value = data
    defaultProviderId.value = data.default_receipt_check_provider_id ?? null
    toast.success('Провайдер по умолчанию сохранён')
  } catch (e: any) {
    toast.error(e.response?.data?.detail || 'Не удалось сохранить провайдера')
  } finally {
    defaultProviderSaving.value = false
  }
}

async function toggleReceiptAutoCheck(enabled: boolean) {
  autoCheckSaving.value = true
  try {
    const { data } = await tradersService.toggleReceiptAutoCheck(enabled)
    traderProfile.value = data
    toast.success(enabled ? 'Авто-проверка включена' : 'Авто-проверка выключена')
  } catch (e: any) {
    toast.error(e.response?.data?.detail || 'Не удалось переключить авто-проверку')
  } finally {
    autoCheckSaving.value = false
  }
}

onMounted(() => {
  previewInterval = setInterval(() => { previewTick.value++ }, 1000)
  void loadReceiptCheckBlock()
})
onUnmounted(() => {
  if (previewInterval) clearInterval(previewInterval)
})

const currentTzPreview = computed(() => {
  // touch reactive tick so preview re-renders every second
  void previewTick.value
  const tz = tzValue.value || getBrowserTz()
  try {
    return dayjs().tz(tz).format('DD.MM.YYYY HH:mm:ss')
  } catch {
    return dayjs().format('DD.MM.YYYY HH:mm:ss')
  }
})

async function saveTimezone() {
  tzLoading.value = true
  try {
    const tz = tzValue.value || null
    await usersService.updateMe({ timezone: tz })
    await authStore.fetchUser()
    toast.success('Часовой пояс сохранён')
    // Reload so all open tables/forms re-render dates with the new timezone.
    window.location.reload()
  } catch (e: any) {
    toast.error(e.response?.data?.detail || 'Ошибка сохранения часового пояса')
  } finally {
    tzLoading.value = false
  }
}

async function changePassword() {
  if (pwForm.new_password !== pwForm.new_password_confirm) {
    toast.error('Пароли не совпадают')
    return
  }
  pwLoading.value = true
  try {
    await usersService.changePassword(pwForm.new_password, pwForm.google_code)
    toast.success('Пароль изменён')
    pwForm.new_password = ''
    pwForm.new_password_confirm = ''
    pwForm.google_code = ''
  } catch (e: any) {
    toast.error(e.response?.data?.detail || 'Ошибка смены пароля')
  } finally { pwLoading.value = false }
}

async function startSetup2FA() {
  setupLoading.value = true
  try {
    const { data } = await usersService.setup2FA()
    setup2FA.value = data
  } catch { toast.error('Ошибка получения 2FA') }
  finally { setupLoading.value = false }
}

async function enable2FA() {
  if (!setup2FA.value) return
  enableLoading.value = true
  try {
    await usersService.enable2FA(setup2FA.value.secret, totpCode.value)
    toast.success('2FA включена!')
    await authStore.fetchUser()
    setup2FA.value = null
  } catch (e: any) {
    toast.error(e.response?.data?.detail || 'Неверный код')
  } finally { enableLoading.value = false }
}
</script>
