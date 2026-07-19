<template>
  <main class="sp-panel-grid min-h-screen overflow-hidden bg-bg-main text-text-main">
    <section class="relative grid min-h-[760px] items-center overflow-hidden px-5 py-24 lg:min-h-screen lg:py-28">
      <img
        :src="publicAsset('brand/hero-fintech.png')"
        alt=""
        class="absolute inset-0 h-full w-full object-cover object-[68%_center] opacity-40 saturate-[0.68] contrast-110 brightness-[0.78]"
      />
      <div class="absolute inset-0 bg-[linear-gradient(90deg,rgba(13,13,13,0.98)_0%,rgba(13,13,13,0.9)_48%,rgba(13,13,13,0.42)_78%,rgba(13,13,13,0.92)_100%),linear-gradient(180deg,rgba(13,13,13,0.5)_0%,rgba(13,13,13,0.18)_48%,#0D0D0D_100%)]" />

      <div class="absolute inset-x-0 top-0 z-10 border-b border-accent/10 bg-bg-main/70 backdrop-blur-2xl">
        <div class="mx-auto flex h-20 w-[calc(100%_-_2rem)] max-w-[1180px] items-center justify-between">
          <RouterLink to="/" class="flex items-center gap-3">
            <img :src="publicAsset('logos/logo.svg')" alt="Логотип SexPayments" class="h-11 w-11" />
            <span class="text-lg font-black">Sex<span class="text-accent">Payments</span></span>
          </RouterLink>
          <div class="hidden items-center gap-6 text-xs font-black uppercase tracking-[0.14em] text-text-muted md:flex">
            <span>Процессинг</span>
            <span>Риски</span>
            <span>Выплаты</span>
          </div>
          <RouterLink
            to="/login"
            class="rounded-xl border border-accent/40 px-5 py-3 text-sm font-black text-accent transition hover:border-accent hover:bg-accent/10 hover:text-text-main"
          >
            Войти
          </RouterLink>
        </div>
      </div>

      <div class="relative z-10 mx-auto grid w-full max-w-[1180px] gap-10 lg:grid-cols-[minmax(0,0.78fr)_420px] lg:items-end">
        <div class="max-w-[820px]">
          <p class="mb-6 sp-kicker">
            Приватная платежная сеть
          </p>
          <h1 class="mb-7 text-[clamp(2.35rem,5.8vw,5rem)] font-black leading-none">
            Платежная инфраструктура для бизнеса 18+
          </h1>
          <p class="mb-8 text-[clamp(1.1rem,2.2vw,1.72rem)] font-bold text-text-secondary">
            Конфиденциально. Быстро. Надежно.
          </p>
          <div class="flex flex-col gap-3 sm:flex-row">
            <RouterLink
              to="/login"
              class="inline-flex min-h-[58px] items-center justify-center gap-2 rounded-xl border border-accent/25 bg-[linear-gradient(135deg,rgba(214,163,143,0.18),transparent_38%),linear-gradient(180deg,#9E1B43_0%,#74112E_100%)] px-7 text-base font-black text-text-main shadow-[0_18px_42px_rgba(139,21,56,0.32)] transition hover:-translate-y-0.5 hover:border-accent/45"
            >
              Начать прием платежей
              <ArrowRight class="h-5 w-5" />
            </RouterLink>
            <a
              href="mailto:sales@sexpayments.example"
              class="inline-flex min-h-[58px] items-center justify-center rounded-xl border border-accent/60 bg-bg-main/30 px-7 text-base font-black text-accent backdrop-blur transition hover:-translate-y-0.5 hover:border-accent hover:bg-accent/10 hover:text-text-main"
            >
              Назначить демо
            </a>
          </div>
        </div>

        <aside class="sp-command-bar overflow-hidden rounded-[1.2rem] border p-3">
          <div class="mb-3 flex items-center justify-between px-2 pt-1">
            <span class="sp-kicker">Платежный контур</span>
            <span class="flex items-center gap-2 text-xs font-black text-status-success">
              <span class="sp-status-dot" />
              активен
            </span>
          </div>
          <div
            v-for="item in ledger"
            :key="item.label"
            class="grid min-h-[58px] grid-cols-[0.8fr_1fr] items-center gap-3 rounded-xl px-4 text-text-muted [&+&]:border-t [&+&]:border-accent/10"
          >
            <span>{{ item.label }}</span>
            <strong class="text-right text-sm text-text-main">{{ item.value }}</strong>
          </div>
          <div class="mt-3 grid grid-cols-5 gap-2 px-2 pb-1">
            <span
              v-for="node in 10"
              :key="node"
              class="h-1.5 rounded-full"
              :class="node % 3 === 0 ? 'bg-accent' : node % 2 === 0 ? 'bg-accent-dark' : 'bg-white/10'"
            />
          </div>
        </aside>
      </div>
    </section>

    <section class="mx-auto -mt-10 grid w-[calc(100%_-_2rem)] max-w-[1180px] grid-cols-1 border-y border-accent/15 bg-bg-main/60 shadow-[0_24px_70px_rgba(0,0,0,0.24)] backdrop-blur sm:grid-cols-2 lg:grid-cols-4">
      <div
        v-for="item in stats"
        :key="item.label"
        class="min-h-32 border-accent/10 p-7 lg:border-r lg:last:border-r-0"
      >
        <strong class="mb-3 block text-4xl font-black">{{ item.value }}</strong>
        <span class="text-text-muted">{{ item.label }}</span>
      </div>
    </section>

    <section class="mx-auto grid w-[calc(100%_-_2rem)] max-w-[1180px] gap-16 py-28 lg:grid-cols-[0.78fr_1fr] lg:items-center">
      <div>
        <p class="mb-5 text-xs font-bold uppercase tracking-[0.18em] text-accent">
          Для серьезных операторов
        </p>
        <h2 class="mb-6 text-[clamp(2.2rem,5vw,4.8rem)] font-black leading-none">
          Один финансовый слой для мерчантов, трейдеров и риск-команд.
        </h2>
        <p class="text-lg leading-8 text-text-muted">
          SexPayments помогает компаниям повышенного риска принимать платежи,
          управлять маршрутами, видеть чистую сверку и не раскрывать лишние
          детали операционного процесса.
        </p>
      </div>

      <div class="rounded-[32px] border border-accent/15 bg-bg-surface/70 p-5 shadow-prime backdrop-blur-xl">
        <div class="mb-5 flex gap-2">
          <span class="h-2.5 w-2.5 rounded-full bg-accent/50" />
          <span class="h-2.5 w-2.5 rounded-full bg-accent/30" />
          <span class="h-2.5 w-2.5 rounded-full bg-accent/20" />
        </div>
        <div class="grid gap-4 sm:grid-cols-2">
          <div class="rounded-[22px] border border-accent/10 bg-bg-main/50 p-6">
            <p class="mb-5 text-text-muted">одобрение</p>
            <strong class="mb-3 block text-5xl font-black">94.7%</strong>
            <span class="text-sm font-bold text-accent">+6.4% за месяц</span>
          </div>
          <div class="rounded-[22px] border border-accent/10 bg-bg-main/50 p-6">
            <p class="mb-5 text-text-muted">оборот</p>
            <strong class="mb-3 block text-5xl font-black">8.42 млн</strong>
            <span class="text-sm font-bold text-accent">закрытая сверка</span>
          </div>
          <div class="rounded-[22px] border border-accent/10 bg-bg-main/50 p-6 sm:col-span-2">
            <div class="mb-4 h-2.5 w-4/5 rounded-full bg-gradient-to-r from-accent-dark via-accent to-white/10" />
            <div class="mb-4 h-2.5 w-2/3 rounded-full bg-white/10" />
            <div class="mb-5 h-2.5 w-3/4 rounded-full bg-gradient-to-r from-accent-dark via-accent to-white/10" />
            <div class="flex justify-between text-xs text-text-muted">
              <span>вход</span>
              <span>проверка</span>
              <span>выплата</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="mx-auto w-[calc(100%_-_2rem)] max-w-[1180px] pb-28">
      <div class="mb-12 max-w-2xl">
        <p class="mb-5 text-xs font-bold uppercase tracking-[0.18em] text-accent">
          Ключевые возможности
        </p>
        <h2 class="text-[clamp(2.2rem,5vw,4.8rem)] font-black leading-none">
          Премиальный процессинг без шума.
        </h2>
      </div>

      <div class="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-12">
        <article
          v-for="(feature, index) in features"
          :key="feature.title"
          class="min-h-[270px] rounded-[24px] border border-accent/15 bg-bg-surface/70 p-7 shadow-prime backdrop-blur transition hover:-translate-y-1 hover:border-accent/35 xl:col-span-3"
          :class="{
            'xl:col-span-6': index === 0 || index === 4,
            'bg-[linear-gradient(150deg,rgba(139,21,56,0.22),rgba(214,163,143,0.04)),rgba(23,23,23,0.72)]': index === 3 || index === 7,
          }"
        >
          <span class="mb-8 grid h-12 w-12 place-items-center rounded-2xl border border-accent/20 bg-accent-dark/15 text-accent">
            <component :is="feature.icon" class="h-6 w-6" />
          </span>
          <h3 class="mb-3 text-xl font-black">{{ feature.title }}</h3>
          <p class="leading-7 text-text-muted">{{ feature.copy }}</p>
        </article>
      </div>
    </section>

    <section class="mx-auto mb-16 w-[calc(100%_-_2rem)] max-w-[1180px] rounded-[32px] border border-accent/15 bg-[linear-gradient(135deg,rgba(139,21,56,0.24),rgba(214,163,143,0.06)),rgba(23,23,23,0.76)] p-8 shadow-prime backdrop-blur lg:p-16">
      <div class="max-w-3xl">
        <p class="mb-5 text-xs font-bold uppercase tracking-[0.18em] text-accent">
          Запуск без лишнего риска
        </p>
        <h2 class="mb-8 text-[clamp(2.1rem,5vw,4.6rem)] font-black leading-none">
          Соберите приватный платежный контур для нового рынка.
        </h2>
        <div class="flex flex-col gap-3 sm:flex-row">
          <RouterLink
            to="/login"
            class="inline-flex min-h-[58px] items-center justify-center gap-2 rounded-2xl bg-gold-gradient px-7 font-black text-text-main shadow-[0_18px_42px_rgba(139,21,56,0.32)] transition hover:-translate-y-0.5"
          >
            Начать прием платежей
            <ArrowRight class="h-5 w-5" />
          </RouterLink>
          <a
            href="mailto:sales@sexpayments.example"
            class="inline-flex min-h-[58px] items-center justify-center rounded-2xl border border-accent/60 px-7 font-black text-accent transition hover:-translate-y-0.5 hover:border-accent hover:bg-accent/10 hover:text-text-main"
          >
            Назначить демо
          </a>
        </div>
      </div>
    </section>
  </main>
</template>

<script setup lang="ts">
import { RouterLink } from 'vue-router'
import {
  ArrowRight,
  BadgeCheck,
  BarChart3,
  Code2,
  CreditCard,
  Globe2,
  Headphones,
  ShieldCheck,
  Zap,
} from 'lucide-vue-next'
import { publicAsset } from '@/utils/assets'

const ledger = [
  { label: 'маршрут', value: 'активен' },
  { label: 'резерв', value: 'под контролем' },
  { label: 'скоринг', value: 'низкий риск' },
]

const stats = [
  { value: '99.98%', label: 'целевой уровень доступности' },
  { value: '120+', label: 'направлений для выплат' },
  { value: '24/7', label: 'мониторинг рисков мерчанта' },
  { value: 'уровень 1', label: 'архитектура с приоритетом защиты' },
]

const features = [
  {
    icon: Zap,
    title: 'Мгновенный процессинг',
    copy: 'Авторизация, маршрутизация и закрытие операций с низкой задержкой.',
  },
  {
    icon: Code2,
    title: 'Гибкая интеграция',
    copy: 'Понятные методы, уведомления и тестовая среда для продуктовых команд.',
  },
  {
    icon: Globe2,
    title: 'Международные платежи',
    copy: 'Работа с разными направлениями и аккуратная логика выбора маршрута.',
  },
  {
    icon: BadgeCheck,
    title: 'Высокое одобрение',
    copy: 'Настройка мерчантов, каскадов и резервов для устойчивой конверсии.',
  },
  {
    icon: ShieldCheck,
    title: 'Приватность по умолчанию',
    copy: 'Дискретная обработка данных, контроль доступа и защита операций.',
  },
  {
    icon: Headphones,
    title: 'Выделенная поддержка',
    copy: 'Помощь с запуском, спорными операциями, лимитами и ежедневной работой.',
  },
  {
    icon: BarChart3,
    title: 'Управление рисками',
    copy: 'Сигналы фрода, резервы, спорные платежи и поведение трафика в реальном времени.',
  },
  {
    icon: CreditCard,
    title: 'Кабинет мерчанта',
    copy: 'Одобрения, балансы, выплаты, споры и здоровье операций в одной панели.',
  },
]
</script>
