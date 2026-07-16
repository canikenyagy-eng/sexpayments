<template>
  <div class="relative inline-block" ref="container">
    <button
      type="button"
      class="flex h-10 items-center justify-between gap-2 rounded-xl border border-border bg-bg-surface px-3 py-2 text-sm text-text-main transition-colors hover:bg-bg-hover focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent disabled:cursor-not-allowed disabled:opacity-50"
      :class="[{ 'text-text-muted': !modelValue }, buttonClass]"
      @click="togglePicker"
    >
      <div class="flex items-center gap-2 overflow-hidden">
        <CalendarIcon v-if="!modelValue" class="h-4 w-4 shrink-0 text-text-muted" />
        <span class="truncate">{{ displayDate }}</span>
      </div>
      <X v-if="modelValue && clearable" class="h-4 w-4 shrink-0 text-text-muted hover:text-text-main" @click.stop="clear" />
    </button>

    <Transition name="fade">
      <div
        v-if="isOpen"
        class="absolute z-50 mt-1 w-[280px] max-w-[calc(100vw-2rem)] rounded-xl border border-border bg-bg-surface p-3 shadow-lg"
        :class="effectiveAlign === 'right' ? 'right-0' : 'left-0'"
      >
        <div class="mb-4 flex items-center justify-between">
          <button type="button" class="flex h-7 w-7 items-center justify-center rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-main" @click="prevMonth">
            <ChevronLeft class="h-4 w-4" />
          </button>
          <div class="text-sm font-bold text-text-main">{{ monthName }} {{ year }}</div>
          <button type="button" class="flex h-7 w-7 items-center justify-center rounded-lg hover:bg-bg-hover text-text-muted hover:text-text-main" @click="nextMonth">
            <ChevronRight class="h-4 w-4" />
          </button>
        </div>

        <div class="grid grid-cols-7 gap-1 text-center text-xs font-medium text-text-muted">
          <div v-for="day in ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']" :key="day" class="w-8">{{ day }}</div>
        </div>

        <div class="mt-2 grid grid-cols-7 gap-1">
          <div v-for="blank in emptyDays" :key="'blank-' + blank" class="h-8 w-8" />
          <!--
            Today highlight: explicit ring + tinted background + accent text
            so the cell is obviously "today" against the dark surface (plain
            text-accent was hard to spot). When today is also the selected
            day, the solid fill already conveys it, so the ring is dropped.
          -->
          <button
            v-for="date in daysInMonth"
            :key="date"
            type="button"
            class="flex h-8 w-8 items-center justify-center rounded-lg text-sm transition-colors"
            :class="[
              isSelected(date) ? 'bg-accent text-white font-bold shadow-prime' : 'hover:bg-bg-hover text-text-main',
              isToday(date) && !isSelected(date) ? 'text-accent font-bold ring-1 ring-inset ring-accent/70 bg-accent/10' : ''
            ]"
            @click="selectDate(date)"
          >
            {{ date }}
          </button>
        </div>

        <div v-if="withTime" class="mt-4 flex items-center gap-2 border-t border-border pt-3">
          <Clock class="h-4 w-4 text-text-muted" />
          <input
            v-model="timeValue"
            type="time"
            class="flex-1 rounded-lg border border-border bg-bg-main px-2 py-1 text-sm text-text-main outline-none focus:border-accent"
            @change="updateTime"
          />
        </div>
      </div>
    </Transition>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, watch, nextTick } from 'vue'
import { onClickOutside } from '@vueuse/core'
import { Calendar as CalendarIcon, ChevronLeft, ChevronRight, X, Clock } from 'lucide-vue-next'
import dayjs, { type Dayjs } from 'dayjs'
import { getUserTz } from '@/utils/datetime'

const props = withDefaults(defineProps<{
  modelValue: string | null
  placeholder?: string
  buttonClass?: string
  align?: 'left' | 'right'
  clearable?: boolean
  withTime?: boolean
}>(), {
  placeholder: 'Выберите дату',
  align: 'left',
  clearable: true,
  withTime: false
})

const emit = defineEmits<{
  'update:modelValue': [value: string | null]
  'change': [value: string | null]
}>()

const container = ref<HTMLElement | null>(null)
const isOpen = ref(false)
const autoAlignRight = ref(false)

const effectiveAlign = computed<'left' | 'right'>(() => {
  if (props.align === 'right') return 'right'
  return autoAlignRight.value ? 'right' : 'left'
})

function updateAlignment() {
  const wrapper = container.value
  if (!wrapper) return
  const rect = wrapper.getBoundingClientRect()
  const panelWidth = 280
  const margin = 16
  autoAlignRight.value = rect.left + panelWidth + margin > window.innerWidth
}

async function togglePicker() {
  if (isOpen.value) {
    isOpen.value = false
    return
  }
  updateAlignment()
  isOpen.value = true
  await nextTick()
  updateAlignment()
}

onClickOutside(container, () => {
  isOpen.value = false
})

const userTz = computed(() => getUserTz())

// All Dayjs objects below are kept in the user's preferred timezone so that
// the calendar grid, "today" highlight, and emitted wall-time strings all
// agree with what the user expects to see.
const currentDate = ref<Dayjs>(dayjs().tz(userTz.value))
const selectedDate = ref<Dayjs | null>(null)
const timeValue = ref('00:00')

watch(() => props.modelValue, (val) => {
  if (val) {
    // Incoming format from this picker is wall-time in user TZ.
    const d = dayjs.tz(val, userTz.value)
    if (d.isValid()) {
      selectedDate.value = d
      currentDate.value = d
      timeValue.value = d.format('HH:mm')
      return
    }
  }
  selectedDate.value = null
  timeValue.value = '00:00'
}, { immediate: true })

const displayDate = computed(() => {
  if (!selectedDate.value) return props.placeholder
  return props.withTime
    ? selectedDate.value.format('DD.MM.YYYY HH:mm')
    : selectedDate.value.format('DD.MM.YYYY')
})

const monthName = computed(() => {
  const raw = currentDate.value.toDate().toLocaleString('ru-RU', { month: 'long', timeZone: userTz.value })
  return raw.charAt(0).toUpperCase() + raw.slice(1)
})

const year = computed(() => currentDate.value.year())

const daysInMonth = computed(() => currentDate.value.daysInMonth())

const emptyDays = computed(() => {
  const firstDayOfMonth = currentDate.value.startOf('month').day()
  // dayjs day(): 0 = Sunday, 1 = Monday … 6 = Saturday. We want week to start on Monday.
  return firstDayOfMonth === 0 ? 6 : firstDayOfMonth - 1
})

function prevMonth() {
  currentDate.value = currentDate.value.subtract(1, 'month').startOf('month')
}

function nextMonth() {
  currentDate.value = currentDate.value.add(1, 'month').startOf('month')
}

function isSelected(day: number) {
  if (!selectedDate.value) return false
  return (
    selectedDate.value.date() === day &&
    selectedDate.value.month() === currentDate.value.month() &&
    selectedDate.value.year() === currentDate.value.year()
  )
}

function isToday(day: number) {
  const today = dayjs().tz(userTz.value)
  return (
    today.date() === day &&
    today.month() === currentDate.value.month() &&
    today.year() === currentDate.value.year()
  )
}

function selectDate(day: number) {
  let d = currentDate.value.date(day)
  if (props.withTime) {
    const [h, m] = timeValue.value.split(':').map(Number)
    d = d.hour(h || 0).minute(m || 0).second(0).millisecond(0)
  } else {
    d = d.hour(0).minute(0).second(0).millisecond(0)
  }
  selectedDate.value = d
  emitValue()
  if (!props.withTime) {
    isOpen.value = false
  }
}

function updateTime() {
  if (selectedDate.value) {
    const [h, m] = timeValue.value.split(':').map(Number)
    selectedDate.value = selectedDate.value.hour(h || 0).minute(m || 0).second(0).millisecond(0)
    emitValue()
  }
}

function clear() {
  selectedDate.value = null
  emit('update:modelValue', null)
  emit('change', null)
  isOpen.value = false
}

function emitValue() {
  if (!selectedDate.value) return
  const val = props.withTime
    ? selectedDate.value.format('YYYY-MM-DDTHH:mm')
    : selectedDate.value.format('YYYY-MM-DD')
  emit('update:modelValue', val)
  emit('change', val)
}
</script>

<style scoped>
.fade-enter-active,
.fade-leave-active {
  transition: opacity 0.15s ease, transform 0.15s ease;
}
.fade-enter-from,
.fade-leave-to {
  opacity: 0;
  transform: translateY(-5px);
}
</style>
