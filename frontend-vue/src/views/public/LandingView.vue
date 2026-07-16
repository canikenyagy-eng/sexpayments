<template>
  <main class="min-h-screen overflow-hidden bg-bg-main text-text-main">
    <section class="relative flex min-h-[760px] items-center overflow-hidden px-5 py-28 lg:min-h-screen">
      <img
        src="/brand/hero-fintech.png"
        alt=""
        class="absolute inset-0 h-full w-full object-cover object-[62%_center] opacity-70"
      />
      <div class="absolute inset-0 bg-[linear-gradient(90deg,rgba(13,13,13,0.97)_0%,rgba(13,13,13,0.78)_42%,rgba(13,13,13,0.24)_76%,rgba(13,13,13,0.84)_100%),linear-gradient(180deg,rgba(13,13,13,0.18)_0%,#0D0D0D_100%)]" />
      <div class="absolute inset-x-0 top-0 z-10 mx-auto flex w-full max-w-[1180px] items-center justify-between px-5 py-5">
        <RouterLink to="/" class="flex items-center gap-3">
          <img src="/logos/logo.svg" alt="SexPayments Logo" class="h-11 w-11" />
          <span class="text-lg font-black">Sex<span class="text-accent">Payments</span></span>
        </RouterLink>
        <RouterLink
          to="/login"
          class="rounded-full border border-accent/40 px-5 py-3 text-sm font-bold text-accent transition hover:border-accent hover:bg-accent/10 hover:text-text-main"
        >
          Sign in
        </RouterLink>
      </div>

      <div class="relative z-10 mx-auto w-full max-w-[1180px]">
        <div class="max-w-[820px]">
          <p class="mb-6 text-xs font-bold uppercase tracking-[0.18em] text-accent">
            Private high-risk payment infrastructure
          </p>
          <h1 class="mb-7 text-[clamp(3.05rem,8vw,7.2rem)] font-black leading-[0.94]">
            Payment Infrastructure for Adult Businesses
          </h1>
          <p class="mb-9 text-[clamp(1.35rem,2.6vw,2.1rem)] font-bold text-text-secondary">
            Private. Fast. Reliable.
          </p>
          <div class="flex flex-col gap-3 sm:flex-row">
            <RouterLink
              to="/login"
              class="inline-flex min-h-[58px] items-center justify-center gap-2 rounded-full bg-gold-gradient px-7 text-base font-black text-text-main shadow-[0_18px_42px_rgba(139,21,56,0.32)] transition hover:-translate-y-0.5"
            >
              Start Processing
              <ArrowRight class="h-5 w-5" />
            </RouterLink>
            <a
              href="mailto:sales@sexpayments.example"
              class="inline-flex min-h-[58px] items-center justify-center rounded-full border border-accent/60 bg-bg-main/30 px-7 text-base font-black text-accent backdrop-blur transition hover:-translate-y-0.5 hover:border-accent hover:bg-accent/10 hover:text-text-main"
            >
              Book a Demo
            </a>
          </div>
        </div>
      </div>
    </section>

    <section class="mx-auto -mt-16 grid w-[calc(100%_-_2rem)] max-w-[1180px] grid-cols-1 overflow-hidden rounded-[24px] border border-border bg-border shadow-prime sm:grid-cols-2 lg:grid-cols-4">
      <div v-for="item in stats" :key="item.label" class="min-h-32 bg-bg-surface/90 p-7 backdrop-blur">
        <strong class="mb-3 block text-4xl font-black">{{ item.value }}</strong>
        <span class="text-text-muted">{{ item.label }}</span>
      </div>
    </section>

    <section class="mx-auto grid w-[calc(100%_-_2rem)] max-w-[1180px] gap-12 py-28 lg:grid-cols-[0.9fr_1.1fr] lg:items-center">
      <div>
        <p class="mb-5 text-xs font-bold uppercase tracking-[0.18em] text-accent">
          Built for serious operators
        </p>
        <h2 class="mb-6 text-[clamp(2.3rem,5vw,5rem)] font-black leading-none">
          One financial layer for merchants, traders, and risk teams.
        </h2>
        <p class="text-lg leading-8 text-text-muted">
          SexPayments gives high-risk businesses a private processing layer,
          clean reconciliation, controlled routing, and real operational tools
          without fragile payment workflows.
        </p>
      </div>

      <div class="rounded-[28px] border border-accent/15 bg-bg-surface/70 p-5 shadow-prime backdrop-blur-xl">
        <div class="mb-5 flex gap-2">
          <span class="h-2.5 w-2.5 rounded-full bg-accent/50" />
          <span class="h-2.5 w-2.5 rounded-full bg-accent/30" />
          <span class="h-2.5 w-2.5 rounded-full bg-accent/20" />
        </div>
        <div class="grid gap-4 sm:grid-cols-2">
          <div class="rounded-[22px] border border-accent/10 bg-bg-main/50 p-6">
            <p class="mb-5 text-text-muted">Approval rate</p>
            <strong class="mb-3 block text-5xl font-black">94.7%</strong>
            <span class="text-sm font-bold text-accent">+6.4% this month</span>
          </div>
          <div class="rounded-[22px] border border-accent/10 bg-bg-main/50 p-6">
            <p class="mb-5 text-text-muted">Net volume</p>
            <strong class="mb-3 block text-5xl font-black">$8.42M</strong>
            <span class="text-sm font-bold text-accent">settled privately</span>
          </div>
          <div class="rounded-[22px] border border-accent/10 bg-bg-main/50 p-6 sm:col-span-2">
            <div class="mb-4 h-2.5 w-4/5 rounded-full bg-gradient-to-r from-accent-dark via-accent to-white/10" />
            <div class="mb-4 h-2.5 w-2/3 rounded-full bg-white/10" />
            <div class="mb-4 h-2.5 w-3/4 rounded-full bg-gradient-to-r from-accent-dark via-accent to-white/10" />
            <div class="h-px bg-gradient-to-r from-transparent via-accent/60 to-transparent" />
          </div>
        </div>
      </div>
    </section>

    <section class="mx-auto w-[calc(100%_-_2rem)] max-w-[1180px] pb-28">
      <div class="mb-12 max-w-2xl">
        <p class="mb-5 text-xs font-bold uppercase tracking-[0.18em] text-accent">
          Core capabilities
        </p>
        <h2 class="text-[clamp(2.3rem,5vw,4.8rem)] font-black leading-none">
          Premium processing without noise.
        </h2>
      </div>

      <div class="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <article
          v-for="feature in features"
          :key="feature.title"
          class="min-h-[270px] rounded-[24px] border border-accent/15 bg-bg-surface/70 p-7 shadow-prime backdrop-blur transition hover:-translate-y-1 hover:border-accent/35"
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
          Start with confidence
        </p>
        <h2 class="mb-8 text-[clamp(2.2rem,5vw,4.8rem)] font-black leading-none">
          Build a private processing stack for your next market.
        </h2>
        <div class="flex flex-col gap-3 sm:flex-row">
          <RouterLink
            to="/login"
            class="inline-flex min-h-[58px] items-center justify-center gap-2 rounded-full bg-gold-gradient px-7 font-black text-text-main shadow-[0_18px_42px_rgba(139,21,56,0.32)] transition hover:-translate-y-0.5"
          >
            Start Processing
            <ArrowRight class="h-5 w-5" />
          </RouterLink>
          <a
            href="mailto:sales@sexpayments.example"
            class="inline-flex min-h-[58px] items-center justify-center rounded-full border border-accent/60 px-7 font-black text-accent transition hover:-translate-y-0.5 hover:border-accent hover:bg-accent/10 hover:text-text-main"
          >
            Book a Demo
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

const stats = [
  { value: '99.98%', label: 'Infrastructure uptime target' },
  { value: '120+', label: 'Supported payout corridors' },
  { value: '24/7', label: 'Merchant risk monitoring' },
  { value: 'PCI', label: 'Security-first architecture' },
]

const features = [
  {
    icon: Zap,
    title: 'Instant Processing',
    copy: 'Authorize, route, and settle transactions with low-latency payment flows.',
  },
  {
    icon: Code2,
    title: 'API Integration',
    copy: 'Clean APIs, webhooks, and sandbox tooling for production-grade teams.',
  },
  {
    icon: Globe2,
    title: 'Worldwide Payments',
    copy: 'Support international payment corridors with controlled routing logic.',
  },
  {
    icon: BadgeCheck,
    title: 'High Approval Rate',
    copy: 'Improve authorization outcomes with intelligent routing and merchant tuning.',
  },
  {
    icon: ShieldCheck,
    title: 'Privacy First',
    copy: 'Designed around discretion, secure data handling, and merchant confidentiality.',
  },
  {
    icon: Headphones,
    title: 'Dedicated Support',
    copy: 'Specialist operational support for onboarding, chargebacks, and disputes.',
  },
  {
    icon: BarChart3,
    title: 'Risk Management',
    copy: 'Monitor fraud signals, reserve exposure, and transaction behavior in real time.',
  },
  {
    icon: CreditCard,
    title: 'Merchant Dashboard',
    copy: 'See approvals, balances, payouts, and dispute health from one private console.',
  },
]
</script>
