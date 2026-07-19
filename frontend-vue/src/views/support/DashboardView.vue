<template>
  <div class="space-y-6">
    <section class="overflow-hidden rounded-[24px] border border-accent/20 bg-bg-surface/70 p-6 shadow-[0_24px_80px_rgba(0,0,0,0.32)] backdrop-blur-xl">
      <div class="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <div class="max-w-3xl">
          <p class="mb-3 text-xs font-black uppercase tracking-[0.18em] text-accent">
            Операционный доступ
          </p>
          <h1 class="text-3xl font-black leading-tight text-text-main sm:text-4xl">
            Панель саппорта
          </h1>
          <p class="mt-3 max-w-2xl text-base font-semibold text-text-secondary">
            Ограниченный кабинет для проверки чеков и принятия решений по премодерации без полного доступа к админке.
          </p>
        </div>
        <RouterLink
          to="/support/receipt-moderations"
          class="inline-flex items-center justify-center gap-2 rounded-2xl bg-accent px-5 py-3 text-sm font-black text-white shadow-[0_18px_50px_rgba(139,21,56,0.28)] transition hover:bg-accent-dark"
        >
          <ShieldCheck class="h-4 w-4" />
          Открыть чеки
        </RouterLink>
      </div>
    </section>

    <div class="grid gap-4 md:grid-cols-3">
      <RouterLink
        to="/support/receipt-moderations"
        class="group rounded-[24px] border border-border bg-bg-surface/70 p-5 no-underline shadow-[0_16px_50px_rgba(0,0,0,0.22)] backdrop-blur-xl transition hover:border-accent/45 hover:bg-accent/5"
      >
        <div class="mb-4 flex h-11 w-11 items-center justify-center rounded-2xl bg-accent/15 text-accent">
          <ShieldCheck class="h-5 w-5" />
        </div>
        <p class="text-lg font-black text-text-main">Модерация чеков</p>
        <p class="mt-2 text-sm text-text-muted">
          Очередь чеков, история решений, запрос PDF или видео.
        </p>
      </RouterLink>

      <div class="rounded-[24px] border border-border bg-bg-surface/70 p-5 shadow-[0_16px_50px_rgba(0,0,0,0.22)] backdrop-blur-xl">
        <div class="mb-4 flex h-11 w-11 items-center justify-center rounded-2xl bg-accent/15 text-accent">
          <LockKeyhole class="h-5 w-5" />
        </div>
        <p class="text-lg font-black text-text-main">Ограниченные права</p>
        <p class="mt-2 text-sm text-text-muted">
          Нет доступа к балансам, пользователям, настройкам и финансовым операциям.
        </p>
      </div>

      <div class="rounded-[24px] border border-border bg-bg-surface/70 p-5 shadow-[0_16px_50px_rgba(0,0,0,0.22)] backdrop-blur-xl">
        <div class="mb-4 flex h-11 w-11 items-center justify-center rounded-2xl bg-accent/15 text-accent">
          <Clock3 class="h-5 w-5" />
        </div>
        <p class="text-lg font-black text-text-main">Быстрое решение</p>
        <p class="mt-2 text-sm text-text-muted">
          Решение саппорта записывается в ту же историю, что и действия support-бота.
        </p>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { Clock3, LockKeyhole, ShieldCheck } from 'lucide-vue-next'
</script>
