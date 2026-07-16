<template>
  <BaseModal
    :model-value="modelValue"
    :title="`Ордер #${order?.id ?? ''}`"
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
        :expires-at="order.date_end"
      />

      <!-- Финансы -->
      <Section label="финансы">
        <div class="grid grid-cols-2 gap-2 py-3">
          <Cell label="Прибыль">
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
            <span v-if="order.exchange_rate != null">
              {{ formatAmount(Number(order.exchange_rate)) }}
            </span>
            <span v-else>—</span>
          </Cell>
        </div>
      </Section>

      <!-- Реквизит -->
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
        <Row v-if="order.requisite.nickname" label="название">
          {{ order.requisite.nickname }}
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

      <!-- Клиент — внутренний id + оборот/конверсия за всё время -->
      <Section v-if="clientInfo" label="клиент">
        <Row label="ID">
          <UuidDisplay
            :value="clientInfo.public_id"
            variant="short"
            success-message="ID клиента скопирован"
          />
        </Row>
        <div class="grid grid-cols-2 gap-2 pt-1 pb-3">
          <Cell label="Оборот">
            <Money :amount="clientInfo.turnover_usdt" currency="USDT" mode="code" variant="plain" />
          </Cell>
          <Cell label="Конверсия">{{ formatPercent(clientInfo.conversion) }}</Cell>
        </div>
      </Section>

      <!-- IDs -->
      <Section label="IDs">
        <Row label="uuid">
          <UuidDisplay :value="order.uuid" variant="full" />
        </Row>
      </Section>

      <!-- Время -->
      <Section label="время">
        <Row label="создан">{{ formatDate(order.created_at) }}</Row>
        <Row label="дедлайн">{{ order.date_end ? formatDate(order.date_end) : '—' }}</Row>
        <Row label="завершён">{{ formatDate(order.confirmed_at ?? order.rejected_at) }}</Row>
      </Section>

      <!-- Проверка чека -->
      <div v-if="check" class="rounded-xl border border-border bg-bg-card p-3">
        <ReceiptCheckResultBlock :check="check" />
      </div>
    </div>

    <template #footer>
      <BaseButton
        v-if="order?.receipt_file"
        variant="ghost"
        @click="order && $emit('check-receipt', order.uuid)"
      >
        Проверить чек
      </BaseButton>
      <!-- Late payment: settle an already failed/canceled order to success. -->
      <BaseButton
        v-if="order && ['failed', 'canceled'].includes(order.status)"
        variant="gold"
        :loading="settling"
        @click="$emit('settle-failed', order)"
      >
        Оплачено
      </BaseButton>
      <BaseButton variant="dark" @click="$emit('update:modelValue', false)">Закрыть</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import UuidDisplay from '@/components/ui/UuidDisplay.vue'
import Money from '@/components/ui/Money.vue'
import PaymentOptionLogo from '@/components/ui/PaymentOptionLogo.vue'
import OrderStatusHero from '@/components/modals/OrderStatusHero.vue'
import Section from '@/components/modals/OrderSection.vue'
import Row from '@/components/modals/OrderRow.vue'
import Cell from '@/components/modals/OrderCell.vue'
import ReceiptCheckResultBlock from '@/components/receipt-check/ReceiptCheckResultBlock.vue'
import { clientsService } from '@/api/services/clients.service'
import { formatAmount, formatDate, formatPercent } from '@/utils/format'
import { ref, watch } from 'vue'
import type { Order, ReceiptCheck, TraderOrderClientInfo } from '@/types'

const props = defineProps<{
  modelValue: boolean
  order: Order | null
  check?: ReceiptCheck | null
  settling?: boolean
}>()

defineEmits<{
  'update:modelValue': [value: boolean]
  'check-receipt': [uuid: string]
  'settle-failed': [order: Order]
}>()

const clientInfo = ref<TraderOrderClientInfo | null>(null)

watch(
  () => [props.modelValue, props.order?.id] as const,
  async ([open, orderId]) => {
    clientInfo.value = null
    if (!open || !orderId) return
    try {
      const { data } = await clientsService.getForOrderMine(orderId)
      // Ignore a late response if the modal has since moved to another order.
      if (props.order?.id === orderId) clientInfo.value = data
    } catch {
      clientInfo.value = null
    }
  },
  { immediate: true },
)
</script>
