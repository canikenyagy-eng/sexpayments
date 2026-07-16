<template>
  <BaseModal
    :model-value="isOpen"
    :title="options.title || 'Подтверждение'"
    :z-index="60"
    @update:model-value="onUpdate"
  >
    <p class="text-text-secondary">{{ options.message }}</p>
    <template #footer>
      <BaseButton variant="dark" @click="handleCancel">{{ options.cancelText || 'Отмена' }}</BaseButton>
      <BaseButton :variant="options.variant || 'danger'" @click="handleConfirm">{{ options.confirmText || 'Подтвердить' }}</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import BaseModal from './BaseModal.vue'
import BaseButton from './BaseButton.vue'
import { useConfirm } from '@/composables/useConfirm'

const { isOpen, options, handleConfirm, handleCancel } = useConfirm()

function onUpdate(val: boolean) {
  if (!val) handleCancel()
}
</script>
