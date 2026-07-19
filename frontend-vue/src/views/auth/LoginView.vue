<template>
  <section class="sp-panel-grid relative flex min-h-screen items-center justify-center overflow-hidden bg-bg-main px-4 py-8">
    <img
      :src="publicAsset('brand/hero-fintech.png')"
      alt=""
      class="absolute inset-0 h-full w-full object-cover object-[62%_center] opacity-35 saturate-[0.65] contrast-110"
    />
    <div class="absolute inset-0 bg-[linear-gradient(90deg,rgba(13,13,13,0.98)_0%,rgba(13,13,13,0.92)_48%,rgba(13,13,13,0.72)_100%),linear-gradient(180deg,rgba(139,21,56,0.18)_0%,transparent_46%,#0D0D0D_100%)]" />
    <div class="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/60 to-transparent" />

    <div class="relative z-10 grid w-full max-w-[1120px] items-center gap-8 lg:grid-cols-[minmax(0,0.9fr)_430px]">
      <div class="hidden max-w-[620px] lg:block">
        <div class="mb-8 flex items-center gap-3">
          <img :src="publicAsset('logos/logo.svg')" alt="Логотип SexPayments" class="h-12 w-12" />
          <div>
            <div class="text-2xl font-black leading-none text-text-main">
              Sex<span class="text-accent">Payments</span>
            </div>
            <div class="mt-1 sp-kicker">Приватная платежная сеть</div>
          </div>
        </div>

        <p class="mb-5 sp-kicker">
          Доступ к закрытому контуру
        </p>
        <h1 class="mb-5 text-[clamp(2.8rem,4.4vw,4.95rem)] font-black leading-[0.94] text-text-main">
          Финансовая инфраструктура без публичного шума
        </h1>
        <p class="max-w-xl text-xl font-bold leading-8 text-text-secondary">
          Кабинеты мерчанта, трейдера, тимлида и поддержки работают поверх одной защищенной платежной базы.
        </p>

        <div class="mt-8 grid max-w-xl gap-1.5 rounded-[1.15rem] border border-accent/15 bg-bg-main/55 p-2 shadow-[0_24px_70px_rgba(0,0,0,0.32)] backdrop-blur-xl">
          <div
            v-for="item in accessSignals"
            :key="item.label"
            class="grid min-h-[46px] grid-cols-[0.8fr_1fr] items-center gap-4 rounded-xl px-4 text-sm"
          >
            <span class="font-semibold text-text-muted">{{ item.label }}</span>
            <strong class="text-right font-black text-text-main">{{ item.value }}</strong>
          </div>
        </div>
      </div>

      <div class="w-full overflow-hidden rounded-[1.35rem] border border-accent/20 bg-bg-surface/80 p-5 shadow-[0_30px_90px_rgba(0,0,0,0.52),inset_0_1px_0_rgba(245,245,245,0.04)] backdrop-blur-xl sm:p-6">
        <div class="mb-7 flex items-center justify-center gap-3 lg:hidden">
          <img :src="publicAsset('logos/logo.svg')" alt="Логотип SexPayments" class="h-11 w-11" />
          <div class="text-[2rem] font-black leading-none text-text-main">
            Sex<span class="text-accent">Payments</span>
          </div>
        </div>
        <div class="mb-5 hidden lg:block">
          <p class="sp-kicker mb-2">Защищенный вход</p>
          <h2 class="text-2xl font-black leading-none text-text-main">Панель доступа</h2>
        </div>

        <div
          v-if="isDemoMode"
          class="mb-4 rounded-[1rem] border border-accent/15 bg-bg-main/45 p-3 text-sm text-text-secondary"
        >
          <p class="mb-3 sp-kicker">Демо-доступ</p>
          <div class="grid gap-1.5">
            <button
              v-for="account in demoAccounts"
              :key="account.username"
              type="button"
              class="grid gap-0.5 rounded-xl border border-accent/10 bg-bg-surface/35 px-3 py-1.5 text-left transition hover:border-accent/35 hover:bg-accent/10"
              @click="fillDemoCredentials(account)"
            >
              <span class="text-xs font-black uppercase tracking-[0.12em] text-accent">{{ account.label }}</span>
              <span class="font-mono text-xs text-text-muted">
                {{ account.username }} / {{ account.password }}
              </span>
            </button>
          </div>
        </div>

        <div
          class="overflow-hidden rounded-2xl transition-all duration-200"
          :class="error ? 'mb-5 max-h-24 opacity-100' : 'mb-0 max-h-0 opacity-0'"
        >
          <div class="rounded-2xl border border-status-danger/25 bg-status-danger/10 px-4 py-3 text-sm font-bold text-[#f1b1a8]">
            {{ error }}
          </div>
        </div>

        <form class="space-y-4" @submit.prevent="handleLogin">
          <BaseInput
            id="username"
            v-model="form.username"
            label="Логин"
            required
          />

          <BaseInput
            id="password"
            v-model="form.password"
            label="Пароль"
            type="password"
            required
          />

          <BaseInput
            v-if="requires2FA"
            id="totp"
            v-model="form.totp_code"
            label="Код двухфакторной защиты"
            placeholder="123456"
            required
          />

          <BaseButton type="submit" variant="gold" size="lg" class="mt-5 w-full !py-2.5" :loading="loading">
            Открыть контур
          </BaseButton>
        </form>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { ref, reactive } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { demoAccounts, isDemoMode, type DemoAccount } from '@/demo/config'
import { publicAsset } from '@/utils/assets'

const router = useRouter()
const authStore = useAuthStore()

const accessSignals = [
  { label: 'режим', value: 'приватный' },
  { label: 'защита', value: '2FA готова' },
  { label: 'маршрутизация', value: 'активна' },
]

const form = reactive({ username: '', password: '', totp_code: '' })
const error = ref('')
const loading = ref(false)
const requires2FA = ref(false)

function fillDemoCredentials(account: DemoAccount) {
  form.username = account.username
  form.password = account.password
}

const roleHome: Record<string, string> = {
  admin: '/admin',
  support: '/support',
  merchant: '/merchant',
  trader: '/trader',
  teamlead: '/teamlead',
}

async function handleLogin() {
  error.value = ''
  loading.value = true
  try {
    await authStore.login(form.username, form.password, form.totp_code || undefined)
    await router.replace(roleHome[authStore.userRole ?? ''] ?? '/')
  } catch (err: any) {
    const detail = err.response?.data?.detail
    if (err.response?.status === 401 && detail === '2FA code required') {
      requires2FA.value = true
      error.value = 'Введите код из приложения аутентификации'
    } else {
      error.value = typeof detail === 'string' ? detail : 'Неверный логин или пароль'
    }
  } finally {
    loading.value = false
  }
}
</script>
