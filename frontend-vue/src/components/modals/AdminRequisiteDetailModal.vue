<template>
  <BaseModal
    :model-value="modelValue"
    :title="modalTitle"
    size="md"
    @update:model-value="(v) => $emit('update:modelValue', v)"
  >
    <div v-if="requisite" class="space-y-3">
      <!-- Hero block: bank logo + name + method/currency + account number + state pills -->
      <section class="space-y-3 rounded-2xl border border-border bg-bg-card p-4 shadow-prime">
        <div class="flex items-center gap-3">
          <PaymentOptionLogo
            :src="requisite.payment_option?.logo_url ?? null"
            :alt="requisite.payment_option?.name ?? requisite.bank_name"
            :size="48"
            class="shrink-0"
          />
          <div class="min-w-0 flex-1">
            <div class="truncate text-base font-bold text-text-main">
              {{ requisite.payment_option?.name || requisite.bank_name }}
            </div>
            <div class="flex items-center gap-1.5 text-xs text-text-muted">
              <MethodBadge :method="requisite.payment_method" />
              <span class="text-border">·</span>
              <span>{{ requisite.currency }}</span>
            </div>
          </div>
        </div>

        <button
          type="button"
          class="group inline-flex items-center rounded px-1 py-0.5 transition-colors hover:bg-bg-hover active:bg-bg-card"
          title="Скопировать"
          @click="copyAccount"
        >
          <span class="font-mono text-text-main transition-colors group-hover:text-accent">
            {{ requisite.account_number }}
          </span>
        </button>

        <div class="flex flex-wrap items-center gap-2">
          <StatusBadge
            context="requisite"
            :status="requisite.is_archived ? 'archived' : requisite.status"
          />
          <StatusBadge :status="requisite.is_active ? 'active' : 'inactive'" />
        </div>
      </section>

      <!-- Информация (трейдер, название реквизита, имя держателя) -->
      <Section label="информация">
        <Row label="трейдер">
          {{ requisite.trader_login ?? `#${requisite.trader_id}` }}
        </Row>
        <Row v-if="requisite.nickname" label="название">
          {{ requisite.nickname }}
        </Row>
        <Row v-if="requisite.account_holder" label="имя">
          {{ requisite.account_holder }}
        </Row>
      </Section>

      <!-- Лимиты -->
      <Section v-if="requisite.limits" label="лимиты" tight>
        <div class="grid grid-cols-2 gap-2 py-3">
          <!-- Shared limit: day == month, so collapse the two cells into
               one full-width row to avoid duplicating "750 000 / 750 000". -->
          <Cell
            v-if="isSharedLimit"
            class="col-span-2"
            label="лимит"
          >
            {{ formatLimit(requisite.limits.limit_daily) }}
          </Cell>
          <template v-else>
            <Cell label="день">{{ formatLimit(requisite.limits.limit_daily) }}</Cell>
            <Cell label="месяц">{{ formatLimit(requisite.limits.limit_monthly) }}</Cell>
          </template>
          <Cell label="мин. tx">{{ formatLimit(requisite.limits.limit_min_transaction) }}</Cell>
          <Cell label="макс. tx">{{ formatLimit(requisite.limits.limit_max_transaction) }}</Cell>
          <!-- Shared: one оборот cell with the ring indicator pinned to
               the right. Ring is `variant='ring'` (no labels, no hover) —
               surrounding cells already carry the numbers, the ring is
               just at-a-glance progress. -->
          <div
            v-if="isSharedLimit"
            class="col-span-2 flex items-center justify-between gap-3 rounded-lg border border-dashed border-border-divider px-3 py-2"
          >
            <div>
              <div class="text-[11px] text-text-muted">оборот</div>
              <div class="text-base font-bold tabular-nums text-text-main">
                {{ formatLimit(requisite.limits.current_daily_turnover) }}
              </div>
            </div>
            <RequisiteLimits :limits="requisite.limits" variant="ring" />
          </div>
          <template v-else>
            <Cell label="оборот день">{{ formatLimit(requisite.limits.current_daily_turnover) }}</Cell>
            <Cell label="оборот месяц">{{ formatLimit(requisite.limits.current_monthly_turnover) }}</Cell>
          </template>
          <Cell label="в работе">{{ formatLimit(requisite.limits.active_amount) }}</Cell>
          <Cell :label="isSharedLimit ? 'остаток' : 'остаток (день)'">
            {{ formatLimit(remainingDaily) }}
          </Cell>
        </div>
      </Section>
    </div>

    <template #footer>
      <BaseButton variant="ghost" size="sm" @click="$emit('delete')">
        <Trash2 class="mr-1 h-3.5 w-3.5 text-status-danger" />
        <span class="text-status-danger">удалить</span>
      </BaseButton>
      <BaseButton variant="gold" size="sm" @click="$emit('edit')">
        <Pencil class="mr-1 h-3.5 w-3.5" />
        редактировать
      </BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, h, defineComponent } from 'vue'
import { Pencil, Trash2 } from 'lucide-vue-next'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import MethodBadge from '@/components/ui/MethodBadge.vue'
import StatusBadge from '@/components/ui/StatusBadge.vue'
import PaymentOptionLogo from '@/components/ui/PaymentOptionLogo.vue'
import RequisiteLimits from '@/components/ui/RequisiteLimits.vue'
import { formatNumber, copyToClipboard } from '@/utils/format'
import { useToast } from '@/composables/useToast'
import type { Requisite } from '@/types'

const props = defineProps<{
  modelValue: boolean
  requisite: Requisite | null
}>()

defineEmits<{
  'update:modelValue': [value: boolean]
  edit: []
  delete: []
}>()

const toast = useToast()

// Modal header shows the requisite id when one is selected — admins
// reference requisites by id in DB queries / debug, so it's worth pinning
// to the header instead of burying inside an "IDs" section.
const modalTitle = computed(() =>
  props.requisite ? `Реквизит #${props.requisite.id}` : 'Реквизит',
)

// Limit kind matches the same wording as the RequisiteLimits hover tooltip:
//   reset_enabled = true  → "дневной (авто-сброс)"
//   reset_enabled = false → "общий (без сброса)"
// Tinting nudges the eye — "общий" is the heavier commitment so it
// renders as warning, "дневной" as info.
// Shared (non-resetting) limit collapses the day/month cells into one
// — backend keeps both fields populated but they're always equal in
// this mode, and "750 000 / 750 000" reads as a copy-paste bug.
const isSharedLimit = computed(() => !props.requisite?.limits?.reset_enabled)

// Daily remaining = limit_daily − current_daily_turnover − active_amount,
// floored at 0. Mirrors the same math the ring widget uses, so the cell
// stays in sync with the visual.
const remainingDaily = computed(() => {
  const l = props.requisite?.limits
  if (!l) return null
  const max = Number(l.limit_daily ?? 0)
  const used = Number(l.current_daily_turnover ?? 0)
  const active = Number(l.active_amount ?? 0)
  if (!Number.isFinite(max) || max <= 0) return null
  return Math.max(max - used - active, 0)
})

// Limits come as number | string from the API depending on field; coerce
// then format with the standard ru-RU thousand-grouping. Renders "—" when
// the field is null/empty (e.g. unlimited daily limit) rather than "0",
// because "0" is a real, distinct value (a hard cap of zero).
function formatLimit(v: number | string | null | undefined): string {
  if (v === null || v === undefined || v === '') return '—'
  const n = typeof v === 'string' ? Number(v) : v
  if (!Number.isFinite(n)) return '—'
  return formatNumber(n)
}

async function copyAccount() {
  const text = props.requisite?.account_number
  if (!text) return
  try {
    await copyToClipboard(text)
    toast.success('Реквизит скопирован')
  } catch {
    toast.error('Не удалось скопировать')
  }
}

// ─── Local presentational components (shared shape with AdminOrderDetailModal) ─

const Section = defineComponent({
  name: 'RequisiteSection',
  props: {
    label: { type: String, required: true },
    tight: { type: Boolean, default: false },
  },
  setup(p, { slots }) {
    return () => h(
      'section',
      { class: 'rounded-xl border border-border bg-bg-card/40 p-3 pb-1' },
      [
        h(
          'div',
          {
            class: [
              'text-[11px] uppercase tracking-wider text-text-muted',
              p.tight ? '' : 'mb-2',
            ],
          },
          p.label,
        ),
        h('div', { class: 'space-y-1' }, slots.default?.()),
      ],
    )
  },
})

const Cell = defineComponent({
  name: 'RequisiteCell',
  props: { label: { type: String, required: true } },
  setup(p, { slots }) {
    return () => h(
      'div',
      { class: 'rounded-lg border border-dashed border-border-divider px-3 py-2' },
      [
        h('div', { class: 'text-[11px] text-text-muted' }, p.label),
        h(
          'div',
          { class: 'text-base font-bold tabular-nums text-text-main' },
          slots.default?.(),
        ),
      ],
    )
  },
})

const Row = defineComponent({
  name: 'RequisiteRow',
  props: { label: { type: String, required: true } },
  setup(p, { slots }) {
    return () => h(
      'div',
      {
        class:
          'flex items-center justify-between border-b border-dashed border-border-divider py-2 text-sm last:border-b-0',
      },
      [
        h('span', { class: 'text-text-muted' }, p.label),
        h('span', { class: 'font-medium text-text-main' }, slots.default?.()),
      ],
    )
  },
})
</script>
