<template>
  <BaseModal
    :model-value="modelValue"
    title="Сделка"
    size="md"
    @update:model-value="(v) => $emit('update:modelValue', v)"
  >
    <div v-if="order" class="space-y-3">
      <OrderStatusHero
        :status="order.status"
        :payment-method="order.payment_method"
        :amount="order.amount"
        :currency="order.currency"
        :amount-usdt="order.amount_usdt"
      />

      <!-- Финансы -->
      <Section label="финансы" class="mb-0">
        <div class="grid grid-cols-2 gap-2 py-3">
          <Cell label="Прибыль мерчанта">
            <span v-if="order.profit_usdt != null" :class="profitColor">
              <Money
                :amount="order.profit_usdt"
                currency="USDT"
                mode="code"
                variant="plain"
              />
            </span>
            <span v-else>—</span>
          </Cell>
          <Cell label="Комиссия">
            <Money
              v-if="order.fee_usdt != null"
              :amount="order.fee_usdt"
              currency="USDT"
              mode="code"
              variant="plain"
            />
            <span v-else>—</span>
          </Cell>
          <Cell label="Ком. трейдера">
            <Money
              v-if="order.trader_fee_usdt != null"
              :amount="order.trader_fee_usdt"
              currency="USDT"
              mode="code"
              variant="plain"
            />
            <span v-else>—</span>
          </Cell>
          <Cell label="Курс">
            <Money
              v-if="order.exchange_rate != null"
              :amount="order.exchange_rate"
              currency=""
              mode="none"
              variant="plain"
            />
            <span v-else>—</span>
          </Cell>
          <Cell label="Ставка мерчанта">
            <span v-if="merchantPercent != null">{{ merchantPercent }}%</span>
            <span v-else>—</span>
          </Cell>
          <Cell label="Ставка трейдера">
            <span v-if="traderPercent != null">{{ traderPercent }}%</span>
            <span v-else>—</span>
          </Cell>
          <!-- Teamlead rewards total (snapshot; platform-internal). -->
          <Cell v-if="teamleadReward != null" label="Награды тимлидов">
            <Money :amount="teamleadReward" currency="USDT" mode="code" variant="plain" />
          </Cell>
          <!-- Platform profit — the headline number, spanning both columns. -->
          <div class="col-span-2 rounded-lg border border-accent/40 bg-accent/10 px-3 py-2">
            <div class="text-[11px] text-text-muted">Прибыль</div>
            <div class="text-base font-bold" :class="platformProfitColor">
              <Money
                v-if="platformProfit != null"
                :amount="platformProfit"
                currency="USDT"
                mode="code"
                variant="plain"
              />
              <span v-else>—</span>
            </div>
          </div>
        </div>
      </Section>

      <!-- Участники -->
      <Section label="участники">
        <Row label="merchant">{{ merchantLogin }}</Row>
        <Row label="trader">{{ traderLogin }}</Row>
        <Row label="реквизит">
          <span v-if="order.requisite_id">#{{ order.requisite_id }}</span>
          <span v-else class="text-text-muted">—</span>
        </Row>
      </Section>

      <Section v-if="order.requisite" label="реквизит">
        <Row label="банк">
          <span class="inline-flex items-center gap-2">
            <PaymentOptionLogo
              :src="order.requisite.logo_url ?? null"
              :alt="order.requisite.bank_name"
              :size="18"
            />
            <span>{{ order.requisite.bank_name || '—' }}</span>
          </span>
        </Row>
        <Row label="реквизит">
          <UuidDisplay
            v-if="order.requisite.account_number"
            :value="order.requisite.account_number"
            variant="full"
            success-message="Реквизит скопирован"
          />
          <span v-else class="text-text-muted">—</span>
        </Row>
        <Row label="имя">
          <span v-if="order.requisite.account_holder">{{ order.requisite.account_holder }}</span>
          <span v-else class="text-text-muted">—</span>
        </Row>
      </Section>

      <Section v-if="order.client_user_id" label="клиент">
        <Row label="userId">
          <span class="inline-flex items-center gap-2">
            <UuidDisplay
              :value="order.client_user_id"
              variant="full"
              success-message="ClientID скопирован"
            />
            <BaseButton
              variant="icon"
              class="text-status-danger"
              title="Заблокировать клиента"
              @click="$emit('block-client')"
            >
              <Ban class="h-4 w-4" />
            </BaseButton>
          </span>
        </Row>
        <template v-if="clientInfo">
          <Row label="ID">
            <UuidDisplay
              :value="clientInfo.public_id"
              variant="short"
              success-message="ID клиента скопирован"
            />
          </Row>
          <div class="grid grid-cols-2 gap-2 pt-1 pb-3">
            <Cell label="Сделок">{{ clientInfo.total_orders }}</Cell>
            <Cell label="Конверсия">{{ formatPercent(clientInfo.conversion) }}</Cell>
            <Cell label="Оборот" class="col-span-2">
              <Money :amount="clientInfo.turnover_usdt" currency="USDT" mode="code" variant="plain" />
            </Cell>
          </div>
        </template>
      </Section>

      <!-- Идентификаторы — full uuid + merchant's external id, both copyable -->
      <Section label="IDs">
        <Row label="uuid">
          <UuidDisplay :value="order.uuid" variant="full" />
        </Row>
        <Row label="external id">
          <UuidDisplay
            v-if="order.external_id"
            :value="order.external_id"
            variant="full"
            success-message="External ID скопирован"
          />
          <span v-else class="text-text-muted">—</span>
        </Row>
        <Row v-if="order.provider_order_id" label="provider id">
          <UuidDisplay
            :value="order.provider_order_id"
            variant="full"
            success-message="Provider ID скопирован"
          />
        </Row>
      </Section>

      <!-- Награды тимлидов -->
      <Section v-if="teamleadRewards && teamleadRewards.length" label="награды тимлидов">
        <Row v-for="(tl, i) in teamleadRewards" :key="i" :label="tl.name">
          <Money :amount="Number(tl.reward_usdt)" currency="USDT" mode="code" variant="plain" />
        </Row>
      </Section>

      <!-- Время -->
      <Section label="время">
        <Row label="создан">{{ formatDate(order.created_at) }}</Row>
        <Row label="дедлайн">{{ order.date_end ? formatDate(order.date_end) : '—' }}</Row>
        <Row label="завершён">
          {{ formatDate(order.confirmed_at ?? order.rejected_at) }}
        </Row>
      </Section>
    </div>

    <template #footer>
      <BaseButton variant="dark" size="sm" @click="$emit('debug')">debug</BaseButton>
      <BaseButton variant="dark" size="sm" @click="$emit('resend')">resend</BaseButton>
      <BaseButton variant="gold" size="sm" @click="$emit('edit')">
        <Pencil class="mr-1 h-3.5 w-3.5" />
        ред.
      </BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Pencil, Ban } from 'lucide-vue-next'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import Money from '@/components/ui/Money.vue'
import PaymentOptionLogo from '@/components/ui/PaymentOptionLogo.vue'
import OrderStatusHero from '@/components/modals/OrderStatusHero.vue'
import Section from '@/components/modals/OrderSection.vue'
import Row from '@/components/modals/OrderRow.vue'
import Cell from '@/components/modals/OrderCell.vue'
import { formatDate, formatPercent } from '@/utils/format'
import { clientsService } from '@/api/services/clients.service'
import type { Order, OrderClientInfo } from '@/types'

const props = defineProps<{
  modelValue: boolean
  order: Order | null
  merchantLogin: string
  traderLogin: string
  teamleadRewards?: { name: string; reward_usdt: string; side?: string }[]
}>()

defineEmits<{
  'update:modelValue': [value: boolean]
  edit: []
  resend: []
  debug: []
  'block-client': []
}>()

// ─── Client block («Клиент» section) ────────────────────────────────
// Fetched on open — the server gates it by the merchant's «Уникальные клиенты»
// toggle (returns null → section hidden). Kept in the modal (not the parent) so
// both call-sites (orders list + requisite modal) get it for free.
const clientInfo = ref<OrderClientInfo | null>(null)

watch(
  () => [props.modelValue, props.order?.id] as const,
  async ([open, orderId]) => {
    clientInfo.value = null
    if (!open || !orderId || !props.order?.client_user_id) return
    try {
      const { data } = await clientsService.getForOrder(orderId)
      // Ignore a late response if the modal has since moved to another order.
      if (props.order?.id === orderId) clientInfo.value = data
    } catch {
      clientInfo.value = null
    }
  },
  { immediate: true },
)

// Tints profit green when positive, red when negative.
// No explicit "+" sign — color alone carries the polarity (Money still
// shows "-" for negatives via Intl formatting).
const profitColor = computed(() => {
  const v = props.order?.profit_usdt
  if (v == null) return 'text-text-main'
  return Number(v) >= 0 ? 'text-status-success' : 'text-status-danger'
})

// Commission percentages, derived from the money values on the order. amount_usdt is the denominator. Rounded to 0.1.
function _pct(part?: number | string | null, whole?: number | string | null): number | null {
  if (part == null || whole == null) return null
  const w = Number(whole)
  if (!w) return null
  return Math.round((Number(part) / w) * 1000) / 10
}
const merchantPercent = computed(() => _pct(props.order?.fee_usdt, props.order?.amount_usdt))
const traderPercent = computed(() => _pct(props.order?.trader_fee_usdt, props.order?.amount_usdt))

// Platform profit, shown as the headline plate. Prefer the backend financial
// snapshot (``platform_profit_usdt`` = fee − trader_fee − teamlead rewards — the
// TRUE net margin); fall back to the gross fee − trader_fee for orders without a
// snapshot yet (historical / not-yet-settled).
const platformProfit = computed<number | null>(() => {
  const o = props.order
  if (!o) return null
  if (o.platform_profit_usdt != null) return Number(o.platform_profit_usdt)
  if (o.fee_usdt == null) return null
  return Number(o.fee_usdt) - Number(o.trader_fee_usdt ?? 0)
})

// Teamlead reward total (the per-teamlead named breakdown is passed in by the
// parent as `teamleadRewards`, since it resolves teamlead_id → login).
const teamleadReward = computed<number | null>(() => {
  const v = props.order?.teamlead_reward_usdt
  return v == null ? null : Number(v)
})
const platformProfitColor = computed(() => {
  const v = platformProfit.value
  if (v == null) return 'text-text-main'
  return v >= 0 ? 'text-status-success' : 'text-status-danger'
})
</script>
