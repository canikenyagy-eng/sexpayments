<template>
  <BaseModal :modelValue="modelValue" @update:modelValue="$emit('update:modelValue', $event)" title="Создание пользователя">
    <form class="space-y-4" @submit.prevent="submit">
      <BaseInput id="cu-login" v-model="form.username" label="Логин" required />
      <BaseInput id="cu-pass" v-model="form.password" label="Пароль" type="password" required />
      <div class="space-y-1.5">
        <label for="cu-role" class="block text-sm font-semibold text-text-secondary">Роль</label>
        <select id="cu-role" v-model="form.role" class="input-field" required>
          <option value="admin">Админ</option>
          <option value="support">Саппорт</option>
          <option value="merchant">Мерчант</option>
          <option value="trader">Трейдер</option>
          <option value="teamlead">Тимлид</option>
        </select>
      </div>

      <BaseInput
        v-model="form.fee_percent"
        label="Финконфиг (%)"
        type="number"
        placeholder="0"
      />

      <template v-if="form.role === 'trader'">
        <BaseInput
          v-model="form.insurance_deposit"
          label="Страховой депозит"
          type="number"
          placeholder="0"
        />
      </template>

      <template v-if="form.role === 'merchant' || form.role === 'trader'">
        <div class="space-y-1.5">
          <label class="block text-sm font-semibold text-text-secondary">Метод</label>
          <select v-model="form.payment_method" class="input-field">
            <option value="">Не выбран</option>
            <option value="sbp">SBP</option>
            <option value="card">Card</option>
            <option value="sim">SIM</option>
          </select>
        </div>

        <div class="space-y-1.5">
          <label class="block text-sm font-semibold text-text-secondary">Тимлид</label>
          <BaseInput v-model="teamleadSearch" placeholder="Поиск тимлида..." class="mb-2" />
          <select v-model="form.teamlead_id" class="input-field">
            <option value="">Не выбран</option>
            <option v-for="tl in filteredTeamleads" :key="tl.id" :value="tl.id">
              {{ tl.username }} (ID: {{ tl.id }})
            </option>
          </select>
        </div>

        <BaseInput
          v-if="form.teamlead_id"
          v-model="form.teamlead_fee_percent"
          label="Финконфиг тимлида (%)"
          type="number"
          placeholder="0"
        />
      </template>
    </form>
    <template #footer>
      <BaseButton variant="dark" @click="$emit('update:modelValue', false)">Отмена</BaseButton>
      <BaseButton variant="gold" :loading="loading" @click="submit">Создать</BaseButton>
    </template>
  </BaseModal>
</template>

<script setup lang="ts">
import { reactive, ref, computed, onMounted } from 'vue'
import BaseModal from '@/components/ui/BaseModal.vue'
import BaseInput from '@/components/ui/BaseInput.vue'
import BaseButton from '@/components/ui/BaseButton.vue'
import { usersService } from '@/api/services/users.service'
import { useToast } from '@/composables/useToast'
import type { User, UserRole } from '@/types'

defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  created: []
}>()

const toast = useToast()
const loading = ref(false)
const teamleads = ref<User[]>([])
const teamleadSearch = ref('')

const form = reactive({
  username: '',
  password: '',
  role: 'trader' as UserRole,
  fee_percent: '',
  insurance_deposit: '',
  payment_method: '',
  teamlead_id: '' as string | number,
  teamlead_fee_percent: '',
})

const filteredTeamleads = computed(() => {
  if (!teamleadSearch.value) return teamleads.value
  const q = teamleadSearch.value.toLowerCase()
  return teamleads.value.filter(t =>
    t.username.toLowerCase().includes(q) || String(t.id).includes(q)
  )
})

async function loadTeamleads() {
  try {
    const { data } = await usersService.list({ role: 'teamlead', limit: 200 })
    teamleads.value = data
  } catch { /* ignore */ }
}

async function submit() {
  if (!form.username || !form.password) return
  loading.value = true
  try {
    const payload: any = {
      username: form.username,
      password: form.password,
      role: form.role,
    }
    if (form.fee_percent) payload.fee_percent = Number(form.fee_percent)
    if (form.role === 'trader' && form.insurance_deposit) {
      payload.insurance_deposit = Number(form.insurance_deposit)
    }
    if (form.payment_method) payload.payment_method = form.payment_method
    if (form.teamlead_id) {
      payload.teamlead_id = Number(form.teamlead_id)
      if (form.teamlead_fee_percent) {
        payload.teamlead_fee_percent = Number(form.teamlead_fee_percent)
      }
    }

    await usersService.create(payload)
    toast.success('Пользователь создан')
    form.username = ''
    form.password = ''
    form.role = 'trader'
    form.fee_percent = ''
    form.insurance_deposit = ''
    form.payment_method = ''
    form.teamlead_id = ''
    form.teamlead_fee_percent = ''
    emit('update:modelValue', false)
    emit('created')
  } catch (e: any) {
    toast.error(e.response?.data?.detail || 'Ошибка создания')
  } finally {
    loading.value = false
  }
}

onMounted(loadTeamleads)
</script>
