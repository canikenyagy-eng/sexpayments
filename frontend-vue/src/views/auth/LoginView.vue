<template>
  <section class="relative flex min-h-screen items-center justify-center overflow-hidden bg-bg-main px-4 py-10">
    <img
      :src="publicAsset('brand/hero-fintech.png')"
      alt=""
      class="absolute inset-0 h-full w-full object-cover object-[62%_center] opacity-45"
    />
    <div class="absolute inset-0 bg-[radial-gradient(circle_at_24%_18%,_rgba(139,21,56,0.34),_transparent_34rem),linear-gradient(90deg,rgba(13,13,13,0.97)_0%,rgba(13,13,13,0.86)_42%,rgba(13,13,13,0.54)_100%)]" />
    <div class="absolute inset-x-0 top-0 h-px bg-gradient-to-r from-transparent via-accent/60 to-transparent" />

    <div class="relative z-10 grid w-full max-w-[1040px] items-center gap-8 lg:grid-cols-[1fr_462px]">
      <div class="hidden max-w-[560px] lg:block">
        <p class="mb-5 text-xs font-bold uppercase tracking-[0.18em] text-accent">
          Закрытая финтех-инфраструктура
        </p>
        <h1 class="mb-5 text-6xl font-black leading-[0.94] text-text-main">
          Платежная инфраструктура для бизнеса 18+
        </h1>
        <p class="text-2xl font-bold text-text-secondary">Конфиденциально. Быстро. Надежно.</p>
      </div>

      <div class="w-full rounded-[24px] border border-accent/20 bg-bg-surface/80 p-8 shadow-[0_30px_90px_rgba(0,0,0,0.55)] backdrop-blur-xl">
        <div class="mb-6 flex items-center justify-center gap-3">
          <img :src="publicAsset('logos/logo.svg')" alt="Логотип SexPayments" class="h-11 w-11" />
          <div class="text-[2rem] font-black leading-none text-text-main">
            Sex<span class="text-accent">Payments</span>
          </div>
        </div>

        <div
          v-if="isDemoMode"
          class="mb-5 rounded-2xl border border-accent/20 bg-accent-dark/10 p-4 text-sm text-text-secondary"
        >
          <div class="mb-3 flex items-center justify-between gap-3">
            <p class="font-bold text-text-main">Демо-доступ администратора</p>
            <button
              type="button"
              class="shrink-0 rounded-xl border border-accent/40 px-3 py-1.5 text-xs font-bold text-accent transition hover:border-accent hover:bg-accent/10"
              @click="fillDemoCredentials"
            >
              Заполнить
            </button>
          </div>
          <div class="grid gap-2 font-mono text-xs">
            <span>логин: {{ demoCredentials.username }}</span>
            <span>пароль: {{ demoCredentials.password }}</span>
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

        <form class="space-y-5" @submit.prevent="handleLogin">
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

          <BaseButton type="submit" variant="gold" size="lg" class="mt-6 w-full !py-2.5" :loading="loading">
            Войти
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
import { demoCredentials, isDemoMode } from '@/demo/config'
import { publicAsset } from '@/utils/assets'

const router = useRouter()
const authStore = useAuthStore()

const form = reactive({ username: '', password: '', totp_code: '' })
const error = ref('')
const loading = ref(false)
const requires2FA = ref(false)

function fillDemoCredentials() {
  form.username = demoCredentials.username
  form.password = demoCredentials.password
}

const roleHome: Record<string, string> = {
  admin: '/admin',
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
