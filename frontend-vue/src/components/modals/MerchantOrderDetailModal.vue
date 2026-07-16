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
      />

      <!-- Финансы -->
      <Section label="финансы">
        <div class="grid grid-cols-2 gap-2 py-3">
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
          <Cell label="Курс">
            <span v-if="order.exchange_rate != null">
              {{ formatAmount(Number(order.exchange_rate)) }}
            </span>
            <span v-else>—</span>
          </Cell>
        </div>
      </Section>

      <!-- Причина отказа -->
      <div
        v-if="order.rejection_reason"
        class="rounded-xl border border-status-danger/30 bg-status-danger/10 p-3"
      >
        <div class="mb-1 text-[11px] uppercase tracking-wider text-text-muted">причина отказа</div>
        <div class="text-sm text-status-danger">{{ order.rejection_reason }}</div>
      </div>

      <!-- IDs -->
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
      </Section>

      <!-- Время -->
      <Section label="время">
        <Row label="создан">{{ formatDate(order.created_at) }}</Row>
        <Row label="подтверждён">{{ order.confirmed_at ? formatDate(order.confirmed_at) : '—' }}</Row>
      </Section>
    </div>

    <template #footer>
      <BaseButton
        v-if="order?.receipt_file"
        variant="dark"
        @click="order && $emit('open-receipt', order.uuid)"
      >
        Открыть чек
      </BaseButton>
      <BaseButton
        variant="dark"
        :loading="resending"
        @click="order && $emit('resend-callback', order)"
      >
        Переотправить callback
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
import OrderStatusHero from '@/components/modals/OrderStatusHero.vue'
import Section from '@/components/modals/OrderSection.vue'
import Row from '@/components/modals/OrderRow.vue'
import Cell from '@/components/modals/OrderCell.vue'
import { formatAmount, formatDate } from '@/utils/format'
import type { Order } from '@/types'

defineProps<{
  modelValue: boolean
  order: Order | null
  resending?: boolean
}>()

defineEmits<{
  'update:modelValue': [value: boolean]
  'open-receipt': [uuid: string]
  'resend-callback': [order: Order]
}>()
</script>
